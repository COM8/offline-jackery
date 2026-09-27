"""Local Shelly Pro 3EM to HomeWizard P1 compatibility bridge."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import time
from typing import Any

from aiohttp import web
from homeassistant.components import zeroconf
from homeassistant.core import HomeAssistant
from zeroconf import ServiceInfo

from .bridge_listener import listener_for
from .const import LOGGER
from .shelly_reader import BridgeError, ShellyReader, ShellySnapshot, required_number, shelly_rpc_url

__all__ = ["BridgeError", "ShellySolarVaultBridge", "homewizard_measurement", "shelly_rpc_url"]

SERVICE_TYPE = "_hwenergy._tcp.local."
HOMEWIZARD_API_PORT = 80
POLL_SECONDS = 1.0
SERIAL_LENGTH = 12


def normalize_serial(value: str) -> str:
    """Return the canonical HomeWizard serial used by Jackery."""
    serial = value.strip().replace(":", "").replace("-", "").upper()
    if len(serial) != SERIAL_LENGTH or any(char not in "0123456789ABCDEF" for char in serial):
        raise ValueError("Serial must contain exactly 12 hexadecimal digits")
    return serial


def homewizard_measurement(shelly: dict[str, Any], *, invert_power: bool = False) -> dict[str, Any]:
    """Map one EM.GetStatus result to HomeWizard local API v1."""
    sign = -1.0 if invert_power else 1.0
    result: dict[str, Any] = {
        "wifi_ssid": "Home Assistant bridge",
        "wifi_strength": 100,
        "smr_version": 50,
        "meter_model": "Shelly Pro 3EM",
        "total_power_import_kwh": round(required_number(shelly, "total_act") / 1000, 6),
        "total_power_import_t1_kwh": round(required_number(shelly, "total_act") / 1000, 6),
        "total_power_import_t2_kwh": 0,
        "total_power_export_kwh": round(required_number(shelly, "total_act_ret") / 1000, 6),
        "total_power_export_t1_kwh": round(required_number(shelly, "total_act_ret") / 1000, 6),
        "total_power_export_t2_kwh": 0,
        "active_power_w": round(required_number(shelly, "total_act_power") * sign, 3),
    }
    for index, phase in enumerate(("a", "b", "c"), 1):
        result[f"active_power_l{index}_w"] = round(required_number(shelly, f"{phase}_act_power") * sign, 3)
        result[f"active_voltage_l{index}_v"] = round(required_number(shelly, f"{phase}_voltage"), 3)
        result[f"active_current_l{index}_a"] = round(required_number(shelly, f"{phase}_current"), 3)
    return result


def homewizard_json_response(
    value: dict[str, Any],
    *,
    status: int = 200,
    headers: dict[str, str] | None = None,
) -> web.Response:
    """Return compact JSON with the content type emitted by a P1 Meter."""
    body = json.dumps(value, separators=(",", ":"), allow_nan=False).encode()
    return web.Response(
        body=body,
        status=status,
        headers=headers,
        content_type="application/json",
    )


def homewizard_service_info(*, serial: str, address: str, port: int) -> ServiceInfo:
    """Build the complete HomeWizard API v1 mDNS advertisement."""
    serial = normalize_serial(serial)
    instance_name = f"p1meter-{serial[-6:]}"
    return ServiceInfo(
        SERVICE_TYPE,
        f"{instance_name}.{SERVICE_TYPE}",
        addresses=[ipaddress.IPv4Address(address).packed],
        port=port,
        properties={
            "api_enabled": "1",
            "path": "/api/v1",
            "serial": serial,
            "product_name": "P1 Meter",
            "product_type": "HWE-P1",
        },
        server=f"{instance_name.lower()}.local.",
    )


class ShellySolarVaultBridge:
    """Own one poller, HTTP listener, and mDNS advertisement."""

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        host: str,
        serial: str,
        port: int,
        advertise_address: str,
        username: str = "admin",
        password: str = "",
        invert_power: bool = False,
    ) -> None:
        self.hass = hass
        self.reader = ShellyReader(hass, host, username, password)
        self.serial = normalize_serial(serial)
        self.port = port
        self.address = str(ipaddress.IPv4Address(advertise_address))
        self.username = username
        self.password = password
        self.invert_power = invert_power
        self.snapshot = ShellySnapshot()
        self._task: asyncio.Task[None] | None = None
        self._listener = listener_for(hass, port)
        self._route_owner = f"p1:{self.serial}"
        self._routes_claimed = False
        self._service: ServiceInfo | None = None

    async def async_read_shelly(self) -> dict[str, Any]:
        """Read and validate the local Shelly endpoint."""
        return await self.reader.read()

    async def async_start(self) -> None:
        """Start serving before publishing the endpoint."""
        self.reader.start()
        try:
            routes = {
                ("GET", "/api"): self._api,
                ("GET", "/api/"): self._api,
                ("GET", "/api/v1/data"): self._data,
                ("GET", "/api/v1/data/"): self._data,
                ("GET", "/healthz"): self._health,
            }
            routes.update({("HEAD", path): handler for (_, path), handler in list(routes.items())})
            await self._listener.claim(self._route_owner, routes)
            self._routes_claimed = True
        except Exception:
            await self.async_stop()
            raise

        self._task = self.hass.async_create_background_task(self._poll(), f"offline_jackery_bridge_{self.serial}")
        service = homewizard_service_info(
            serial=self.serial,
            address=self.address,
            port=self.port,
        )
        try:
            instance = await zeroconf.async_get_async_instance(self.hass)
            await instance.async_register_service(service)
        except Exception:
            await self.async_stop()
            raise
        self._service = service

    async def async_stop(self) -> None:
        """Withdraw mDNS and release all resources."""
        if self._service is not None:
            instance = await zeroconf.async_get_async_instance(self.hass)
            await instance.async_unregister_service(self._service)
            self._service = None
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        if self._routes_claimed:
            await self._listener.release(self._route_owner)
            self._routes_claimed = False
        await self.reader.close()

    async def _poll(self) -> None:
        while True:
            started = time.monotonic()
            try:
                value = await self.async_read_shelly()
                self.snapshot.record(value)
            except BridgeError as err:
                self.snapshot.error = str(err)
                LOGGER.warning("Shelly bridge %s: %s", self.serial, err)
            await asyncio.sleep(max(0.0, POLL_SECONDS - (time.monotonic() - started)))

    def _current(self) -> tuple[dict[str, Any] | None, str]:
        value, error = self.snapshot.current()
        return (homewizard_measurement(value, invert_power=self.invert_power) if value is not None else None), error

    async def _api(self, _request: web.Request) -> web.Response:
        return homewizard_json_response(
            {
                "product_type": "HWE-P1",
                "product_name": "P1 Meter",
                "serial": self.serial,
                "firmware_version": "offline-jackery-bridge-1",
                "api_version": "v1",
            }
        )

    async def _data(self, _request: web.Request) -> web.Response:
        value, error = self._current()
        return homewizard_json_response(
            value if value is not None else {"status": "unavailable", "error": error},
            status=200 if value is not None else 503,
            headers={"Cache-Control": "no-store"},
        )

    async def _health(self, _request: web.Request) -> web.Response:
        value, error = self._current()
        return homewizard_json_response(
            {"status": "ok", "active_power_w": value["active_power_w"]} if value is not None else {"status": "unavailable", "error": error},
            status=200 if value is not None else 503,
        )
