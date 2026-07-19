"""Local Shelly Pro 3EM to HomeWizard P1 compatibility bridge."""

from __future__ import annotations

import asyncio
import ipaddress
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from aiohttp import ClientError, ClientSession, ClientTimeout, DigestAuthMiddleware, web
from homeassistant.components import zeroconf
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from zeroconf import ServiceInfo

from .const import LOGGER

SERVICE_TYPE = "_hwenergy._tcp.local."
POLL_SECONDS = 1.0
STALE_SECONDS = 5.0
SERIAL_LENGTH = 12


class BridgeError(RuntimeError):
    """The Shelly response or bridge configuration is unusable."""


def normalize_serial(value: str) -> str:
    """Return the canonical HomeWizard serial used by Jackery."""
    serial = value.strip().replace(":", "").replace("-", "").upper()
    if len(serial) != SERIAL_LENGTH or any(char not in "0123456789ABCDEF" for char in serial):
        raise ValueError("Serial must contain exactly 12 hexadecimal digits")
    return serial


def shelly_rpc_url(host: str) -> str:
    """Build a safe Gen2 RPC URL from a host or base URL."""
    raw = host.strip()
    if "://" not in raw:
        raw = f"http://{raw}"
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Enter a valid Shelly hostname, IP address, or HTTP URL")
    # A user-supplied path must not accidentally turn into /foo/rpc/....
    return urlunsplit((parsed.scheme, parsed.netloc, "/rpc/EM.GetStatus", "id=0", ""))


def _number(source: dict[str, Any], key: str) -> float:
    value = source.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        error_message = f"Shelly response is missing numeric field {key!r}"
        raise BridgeError(error_message)
    return float(value)


def homewizard_measurement(shelly: dict[str, Any], *, serial: str, invert_power: bool = False) -> dict[str, Any]:
    """Map one EM.GetStatus result to HomeWizard local API v1."""
    sign = -1.0 if invert_power else 1.0
    result: dict[str, Any] = {
        "wifi_ssid": "Home Assistant bridge",
        "wifi_strength": 100,
        "smr_version": 50,
        "meter_model": "Shelly Pro 3EM via Offline Jackery",
        "unique_id": serial,
        "active_power_w": round(_number(shelly, "total_act_power") * sign, 3),
    }
    for index, phase in enumerate(("a", "b", "c"), 1):
        result[f"active_power_l{index}_w"] = round(_number(shelly, f"{phase}_act_power") * sign, 3)
        result[f"active_voltage_l{index}_v"] = round(_number(shelly, f"{phase}_voltage"), 3)
        result[f"active_current_l{index}_a"] = round(_number(shelly, f"{phase}_current"), 3)
    return result


@dataclass(slots=True)
class BridgeSnapshot:
    measurement: dict[str, Any] | None = None
    updated: float = 0.0
    error: str = "Waiting for the first Shelly reading"


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
        self.url = shelly_rpc_url(host)
        self.serial = normalize_serial(serial)
        self.port = port
        self.address = str(ipaddress.IPv4Address(advertise_address))
        self.username = username
        self.password = password
        self.invert_power = invert_power
        self.snapshot = BridgeSnapshot()
        self._task: asyncio.Task[None] | None = None
        self._runner: web.AppRunner | None = None
        self._service: ServiceInfo | None = None
        self._session: ClientSession | None = None

    async def async_read_shelly(self) -> dict[str, Any]:
        """Read and validate the local Shelly endpoint."""
        temporary_session: ClientSession | None = None
        if self._session is not None:
            session = self._session
        elif self.password:
            temporary_session = ClientSession(middlewares=(DigestAuthMiddleware(self.username, self.password),))
            session = temporary_session
        else:
            session = async_get_clientsession(self.hass)
        try:
            async with session.get(
                self.url,
                timeout=ClientTimeout(total=2),
            ) as response:
                response.raise_for_status()
                value = await response.json(content_type=None)
        except (TimeoutError, ClientError, ValueError) as err:
            error_message = f"Shelly request failed: {err}"
            raise BridgeError(error_message) from err
        finally:
            if temporary_session is not None:
                await temporary_session.close()
        if not isinstance(value, dict):
            raise BridgeError("Shelly returned a non-object JSON value")
        return value

    async def async_start(self) -> None:
        """Start serving before publishing the endpoint."""
        if self.password:
            self._session = ClientSession(middlewares=(DigestAuthMiddleware(self.username, self.password),))
        app = web.Application()
        app.router.add_get("/api", self._api)
        app.router.add_get("/api/", self._api)
        app.router.add_get("/api/v1/data", self._data)
        app.router.add_get("/api/v1/data/", self._data)
        app.router.add_get("/healthz", self._health)
        self._runner = web.AppRunner(app, access_log=None)
        try:
            await self._runner.setup()
            await web.TCPSite(self._runner, "0.0.0.0", self.port).start()
        except Exception:
            await self.async_stop()
            raise

        self._task = self.hass.async_create_background_task(self._poll(), f"offline_jackery_bridge_{self.serial}")
        name = f"p1meter-{self.serial}"
        service = ServiceInfo(
            SERVICE_TYPE,
            f"{name}.{SERVICE_TYPE}",
            addresses=[ipaddress.IPv4Address(self.address).packed],
            port=self.port,
            properties={"serial": self.serial},
            server=f"{name.lower()}.local.",
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
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def _poll(self) -> None:
        while True:
            started = time.monotonic()
            try:
                value = await self.async_read_shelly()
                self.snapshot.measurement = homewizard_measurement(value, serial=self.serial, invert_power=self.invert_power)
                self.snapshot.updated = time.monotonic()
                self.snapshot.error = ""
            except BridgeError as err:
                self.snapshot.error = str(err)
                LOGGER.warning("Shelly bridge %s: %s", self.serial, err)
            await asyncio.sleep(max(0.0, POLL_SECONDS - (time.monotonic() - started)))

    def _current(self) -> tuple[dict[str, Any] | None, str]:
        age = time.monotonic() - self.snapshot.updated
        if self.snapshot.measurement is None:
            return None, self.snapshot.error
        if age > STALE_SECONDS:
            detail = self.snapshot.error or "no response"
            return None, f"Meter data is stale ({age:.1f}s): {detail}"
        return dict(self.snapshot.measurement), self.snapshot.error

    async def _api(self, _request: web.Request) -> web.Response:
        return web.json_response(
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
        return web.json_response(
            value if value is not None else {"status": "unavailable", "error": error},
            status=200 if value is not None else 503,
            headers={"Cache-Control": "no-store"},
        )

    async def _health(self, _request: web.Request) -> web.Response:
        value, error = self._current()
        return web.json_response(
            {"status": "ok", "active_power_w": value["active_power_w"]} if value is not None else {"status": "unavailable", "error": error},
            status=200 if value is not None else 503,
        )
