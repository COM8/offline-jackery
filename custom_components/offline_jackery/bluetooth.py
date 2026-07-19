"""Bluetooth discovery helpers and an async SolarVault connection."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from bleak import BleakClient
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.backends.device import BLEDevice
from bleak_retry_connector import establish_connection

from .bridge import normalize_serial
from .protocol import (
    JACKERY_SERVICE_UUID,
    NOTIFY_CHARACTERISTIC_UUID,
    WRITE_CHARACTERISTIC_UUID,
    PageAssembler,
    build_command_pages,
    decrypt_packet,
    parse_response_page,
)

LOGGER = logging.getLogger(__name__)

READ_DEVICE = (3011, 106)
READ_SYSTEM = (3019, 120)
SET_EPS = (3022, 107)
SET_FEED_GRID_LIMIT = (3029, 121)
SET_FOLLOW_METER = (3044, 121)
BIND_SMART_METER = (3012, 108)


def advertised_serial(manufacturer_data: dict[int, bytes]) -> str | None:
    """
    Decode the plain serial carried in Jackery manufacturer data.

    Android reconstructs the little-endian two-byte manufacturer identifier,
    treats its first byte as a category, and appends the remaining byte to the
    manufacturer payload. Categories 2 and 9 are Jackery devices/accessories.
    """
    for company_id, value in manufacturer_data.items():
        prefix = company_id.to_bytes(2, "little", signed=False)
        if prefix[0] not in {2, 9}:
            continue
        try:
            serial = (prefix[1:] + bytes(value)).decode("ascii").strip("\x00")
        except UnicodeDecodeError:
            continue
        if serial:
            return serial
    return None


def is_jackery(service_uuids: list[str] | tuple[str, ...]) -> bool:
    """Return whether an advertisement exposes Jackery's GATT service."""
    return JACKERY_SERVICE_UUID in {value.lower() for value in service_uuids}


def serial_matches(expected: str, advertised: str | None, name: str | None) -> bool:
    """Match the API serial against decoded advertisement data or local name."""
    wanted = expected.strip().casefold()
    return bool(wanted and ((advertised and advertised.strip().casefold() == wanted) or (name and wanted in name.casefold())))


def merge_dict(target: dict[str, Any], update: dict[str, Any]) -> None:
    """Recursively merge incremental device updates into the latest snapshot."""
    for key, value in update.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            merge_dict(target[key], value)
        else:
            target[key] = value


class SolarVaultClient:
    """One serialized, reconnectable SolarVault BLE command connection."""

    def __init__(
        self,
        device: BLEDevice,
        key: bytes,
        *,
        timeout: float = 10.0,
        disconnected_callback: Callable[[], None] | None = None,
    ) -> None:
        self.device = device
        self._key = key
        self._timeout = timeout
        self._external_disconnect = disconnected_callback
        self._client: BleakClient | None = None
        self._assembler = PageAssembler()
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._command_lock = asyncio.Lock()
        self._write_characteristic: BleakGATTCharacteristic | None = None
        self.telemetry: dict[str, Any] = {}
        self.system: dict[str, Any] = {}

    @property
    def is_connected(self) -> bool:
        return self._client is not None and self._client.is_connected

    async def async_connect(self) -> None:
        """Connect and subscribe to encrypted notifications."""
        if self.is_connected:
            return
        # Home Assistant recommends the retry connector for transient BlueZ and
        # proxy failures. A fresh BleakClient is created for every connection;
        # this SolarVaultClient instance is never reused after disconnect.
        self._client = await establish_connection(
            BleakClient,
            self.device,
            self.device.name or "Jackery device",
            disconnected_callback=self._on_disconnect,
        )
        self._write_characteristic = self._client.services.get_characteristic(WRITE_CHARACTERISTIC_UUID)
        notify = self._client.services.get_characteristic(NOTIFY_CHARACTERISTIC_UUID)
        if self._write_characteristic is None or notify is None:
            await self._client.disconnect()
            raise ConnectionError("Device does not expose the Jackery characteristics")
        await self._client.start_notify(notify, self._on_notification)
        LOGGER.info("Connected to Jackery device at %s", self.device.address)

    async def async_disconnect(self) -> None:
        """Disconnect without failing if the link has already gone away."""
        if self.is_connected and self._client is not None:
            await self._client.disconnect()

    def _on_disconnect(self, _client: BleakClient) -> None:
        error = ConnectionError("Jackery Bluetooth connection closed")
        for future in self._pending.values():
            if not future.done():
                future.set_exception(error)
        self._pending.clear()
        self._write_characteristic = None
        self._client = None
        LOGGER.warning("Jackery Bluetooth connection closed")
        if self._external_disconnect:
            self._external_disconnect()

    def _on_notification(self, _characteristic: BleakGATTCharacteristic, encrypted: bytearray) -> None:
        try:
            page = parse_response_page(decrypt_packet(bytes(encrypted), self._key))
            body = self._assembler.add(page)
            if body is None:
                return
            if page.message_type in {106, 107}:
                merge_dict(self.telemetry, body)
            elif page.message_type in {120, 121}:
                merge_dict(self.system, body)
            future = self._pending.get(page.action_id)
            if future is not None and not future.done():
                future.set_result({"code": page.code, "body": body})
        except Exception as err:  # noqa: BLE001  # Bleak callbacks must never escape into the loop.
            if self._pending:
                for future in self._pending.values():
                    if not future.done():
                        future.set_exception(err)
                LOGGER.warning("Invalid Jackery notification failed command: %s", err)
            else:
                LOGGER.debug("Ignored invalid Jackery notification: %s", err)

    async def _async_command(self, action_id: int, message_type: int, body: dict[str, Any]) -> dict[str, Any]:
        async with self._command_lock:
            if not self.is_connected or self._client is None or self._write_characteristic is None:
                raise ConnectionError("Jackery device is not connected")
            future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
            self._pending[action_id] = future
            try:
                for page in build_command_pages(
                    action_id=action_id,
                    message_type=message_type,
                    body=body,
                    key=self._key,
                ):
                    await self._client.write_gatt_char(self._write_characteristic, page, response=False)
                response = await asyncio.wait_for(future, self._timeout)
            finally:
                self._pending.pop(action_id, None)
            if response["code"] != 0:
                error_message = f"Jackery rejected action {action_id} with code {response['code']}"
                raise RuntimeError(error_message)
            return response["body"]

    async def async_read(self) -> dict[str, dict[str, Any]]:
        """Read all main-device and combined-system properties."""
        telemetry = await self._async_command(*READ_DEVICE, {})
        system = await self._async_command(*READ_SYSTEM, {})
        merge_dict(self.telemetry, telemetry)
        merge_dict(self.system, system)
        return {"telemetry": self.telemetry, "system": self.system}

    async def async_set_eps(self, enabled: bool) -> None:
        """Enable or disable the Off-grid/EPS output."""
        await self._async_command(*SET_EPS, {"swEps": int(enabled)})

    async def async_set_follow_meter(self, enabled: bool) -> None:
        """Enable or disable smart-meter power following."""
        await self._async_command(*SET_FOLLOW_METER, {"isFollowMeterPw": int(enabled)})

    async def async_set_feed_grid_limit(self, power_w: int) -> None:
        """Set the configured grid feed-in ceiling in watts."""
        await self._async_command(*SET_FEED_GRID_LIMIT, {"maxFeedGrid": power_w})

    async def async_bind_local_p1_meter(self, serial: str) -> None:
        """Bind a locally advertised HomeWizard-compatible meter."""
        serial = normalize_serial(serial)
        response = await self._async_command(
            *BIND_SMART_METER,
            {
                "smart": [
                    {
                        "deviceSn": serial,
                        "devType": 4,
                        "subType": 5,
                        "scanName": "p1meter",
                        "param": 0,
                        "linkType": "",
                        "bindKey": 0,
                    }
                ]
            },
        )
        results = response.get("smart")
        if isinstance(results, list):
            failed = [item for item in results if isinstance(item, dict) and item.get("code", 0) != 0]
            if failed:
                error_message = f"Smart-meter binding failed: {failed}"
                raise RuntimeError(error_message)
