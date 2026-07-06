"""Five-second BLE polling and reconnect management."""

from __future__ import annotations

import asyncio
import time
from datetime import timedelta
from typing import Any

from bleak.exc import BleakError
from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .bluetooth import SolarVaultClient
from .const import (
    DOMAIN,
    LOGGER,
    MAX_RETRY_INTERVAL_SECONDS,
    UPDATE_INTERVAL_SECONDS,
)
from .protocol import ProtocolError, decode_bluetooth_key


class ExponentialBackoff:
    """Binary exponential reconnect schedule capped at 64 seconds."""

    def __init__(self, maximum: int = MAX_RETRY_INTERVAL_SECONDS) -> None:
        self.maximum = maximum
        self.failures = 0
        self.next_retry = 0.0

    def failed(self, now: float) -> int:
        """Record a failure and return its retry delay."""

        delay = min(2**self.failures, self.maximum)
        self.failures += 1
        self.next_retry = now + delay
        return delay

    def ready(self, now: float) -> bool:
        """Return whether another automatic connection may be attempted."""

        return now >= self.next_retry

    def reset(self) -> None:
        """Clear failures after success or an explicit user refresh."""

        self.failures = 0
        self.next_retry = 0.0


class OfflineJackeryDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinate one persistent BLE link and all Home Assistant entities."""

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        config_entry: ConfigEntry,
        address: str,
        bluetooth_key: str,
    ) -> None:
        super().__init__(
            hass,
            LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=UPDATE_INTERVAL_SECONDS),
            always_update=True,
            config_entry=config_entry,
        )
        self.address = address
        self._key = decode_bluetooth_key(bluetooth_key)
        self._client: SolarVaultClient | None = None
        self._backoff = ExponentialBackoff()

    @property
    def connected(self) -> bool:
        """Return whether the GATT connection is currently active."""

        return self._client is not None and self._client.is_connected

    def _disconnected(self) -> None:
        """Log unsolicited disconnects; the next update creates a fresh client."""

        LOGGER.info(
            "Jackery device %s disconnected; reconnect will be scheduled",
            self.address,
        )

    async def _async_connect(self) -> SolarVaultClient:
        """Resolve the best HA adapter/proxy and create a fresh Bleak client."""

        device = bluetooth.async_ble_device_from_address(
            self.hass, self.address, connectable=True
        )
        if device is None:
            raise ConnectionError("No connectable adapter can currently reach the device")
        client = SolarVaultClient(
            device,
            self._key,
            disconnected_callback=self._disconnected,
        )
        await client.async_connect()
        self._client = client
        return client

    async def _async_update_data(self) -> dict[str, Any]:
        """Connect if needed and read a full local snapshot."""

        now = time.monotonic()
        if not self._backoff.ready(now):
            remaining = max(1, round(self._backoff.next_retry - now))
            raise UpdateFailed(f"Bluetooth reconnect waiting for backoff ({remaining}s)")
        try:
            client = self._client if self.connected else await self._async_connect()
            data = await client.async_read()
        except (
            BleakError,
            ConnectionError,
            TimeoutError,
            RuntimeError,
            ProtocolError,
        ) as err:
            if self._client is not None:
                await self._client.async_disconnect()
                self._client = None
            delay = self._backoff.failed(time.monotonic())
            LOGGER.warning(
                "Jackery Bluetooth update failed; retrying in %s seconds: %s",
                delay,
                err,
            )
            raise UpdateFailed(f"Bluetooth update failed: {err}") from err
        if self._backoff.failures:
            LOGGER.info("Jackery Bluetooth connection recovered")
        self._backoff.reset()
        return data

    async def async_force_refresh(self) -> None:
        """Bypass reconnect backoff for an explicit user request."""

        self._backoff.reset()
        await self.async_request_refresh()

    async def async_set_eps(self, enabled: bool) -> None:
        """Write EPS state and refresh all related entities."""

        if not self.connected or self._client is None:
            await self.async_force_refresh()
        if self._client is None:
            raise ConnectionError("Jackery device is unavailable")
        await self._client.async_set_eps(enabled)
        await asyncio.sleep(0.2)
        await self.async_force_refresh()

    async def async_set_follow_meter(self, enabled: bool) -> None:
        """Write smart-meter following state and refresh."""

        if not self.connected or self._client is None:
            await self.async_force_refresh()
        if self._client is None:
            raise ConnectionError("Jackery device is unavailable")
        await self._client.async_set_follow_meter(enabled)
        await asyncio.sleep(0.2)
        await self.async_force_refresh()

    async def async_set_feed_grid_limit(self, power_w: int) -> None:
        """Validate and write the grid feed-in ceiling."""

        maximum = nested_value(self.data, "system.maxSysOutPw")
        if not isinstance(maximum, int) or maximum <= 0:
            raise ValueError("Device did not report its maximum system output")
        if power_w < 0 or power_w > maximum or power_w % 10:
            raise ValueError(f"Feed-in limit must be 0-{maximum} W in 10 W steps")
        if not self.connected or self._client is None:
            await self.async_force_refresh()
        if self._client is None:
            raise ConnectionError("Jackery device is unavailable")
        await self._client.async_set_feed_grid_limit(power_w)
        await asyncio.sleep(0.2)
        await self.async_force_refresh()

    async def async_shutdown(self) -> None:
        """Release the GATT connection during unload."""

        if self._client is not None:
            await self._client.async_disconnect()
            self._client = None


def nested_value(data: object, path: str) -> object | None:
    """Read a dot-separated dictionary/list path."""

    current = data
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit():
            index = int(part)
            current = current[index] if index < len(current) else None
        else:
            return None
    return current


def scalar_values(
    value: object, prefix: str = ""
) -> dict[str, str | int | float | bool]:
    """Flatten every scalar BLE property, including list members."""

    result: dict[str, str | int | float | bool] = {}
    if isinstance(value, dict):
        for key, item in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            result.update(scalar_values(item, path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            path = f"{prefix}.{index}" if prefix else str(index)
            result.update(scalar_values(item, path))
    elif isinstance(value, (str, int, float, bool)):
        result[prefix] = value
    return result
