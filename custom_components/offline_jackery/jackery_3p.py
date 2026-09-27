"""Experimental Jackery 3P discovery identity; metering is unverified."""

from __future__ import annotations

import asyncio
import ipaddress
import re
import time

from aiohttp import web
from homeassistant.components import zeroconf
from homeassistant.core import HomeAssistant
from zeroconf import ServiceInfo

from .bridge_listener import listener_for
from .const import LOGGER
from .shelly_reader import BridgeError, ShellyReader, ShellySnapshot

SERVICE_TYPE_3P = "_jackery_power._tcp.local."
PREFIX_3P = "jackery3p"  # Provisional until compared with a physical meter.


def normalize_3p_serial(value: str) -> str:
    serial = value.strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{6,24}", serial):
        raise ValueError("3P serial must contain 6-24 letters or digits")
    return serial


def normalize_bind_key(value: str) -> str:
    key = value.strip().upper()
    if not re.fullmatch(r"[0-9A-F]{8,24}", key) or len(key) % 2:
        raise ValueError("3P bind key must be an even-length hexadecimal value of 8-24 digits")
    return key


def jackery_3p_service_info(*, serial: str, bind_key: str, address: str, port: int) -> ServiceInfo:
    """Build the app-parser-compatible, still provisional DNS-SD record."""
    serial = normalize_3p_serial(serial)
    bind_key = normalize_bind_key(bind_key)
    instance = f"{PREFIX_3P}-{serial}-{bind_key}"
    return ServiceInfo(
        SERVICE_TYPE_3P,
        f"{instance}.{SERVICE_TYPE_3P}",
        addresses=[ipaddress.IPv4Address(address).packed],
        port=port,
        properties={},
        server=f"{instance.lower()}.local.",
    )


class Jackery3PDiscoveryBridge:
    """Advertise a 3P identity without claiming binding or live-meter support."""

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        host: str,
        serial: str,
        bind_key: str,
        port: int,
        advertise_address: str,
        username: str = "admin",
        password: str = "",
    ) -> None:
        self.hass = hass
        self.serial = normalize_3p_serial(serial)
        self.bind_key = normalize_bind_key(bind_key)
        self.port = port
        self.address = str(ipaddress.IPv4Address(advertise_address))
        self.reader = ShellyReader(hass, host, username, password)
        self.snapshot = ShellySnapshot()
        self._listener = listener_for(hass, port)
        self._route_owner = f"3p:{self.serial}"
        self._routes_claimed = False
        self._task: asyncio.Task[None] | None = None
        self._service: ServiceInfo | None = None

    async def async_read_shelly(self) -> dict:
        return await self.reader.read()

    async def async_start(self) -> None:
        self.reader.start()
        try:
            await self._listener.claim(self._route_owner, {("GET", "/api/measurement"): self._measurement})
            self._routes_claimed = True
            self._task = self.hass.async_create_background_task(self._poll(), f"offline_jackery_3p_{self.serial}")
            service = jackery_3p_service_info(
                serial=self.serial, bind_key=self.bind_key, address=self.address, port=self.port
            )
            instance = await zeroconf.async_get_async_instance(self.hass)
            await instance.async_register_service(service)
            self._service = service
        except Exception:
            await self.async_stop()
            raise

    async def async_stop(self) -> None:
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
                self.snapshot.record(await self.reader.read())
            except BridgeError as err:
                self.snapshot.error = str(err)
                LOGGER.warning("3P discovery bridge %s: %s", self.serial, err)
            await asyncio.sleep(max(0.0, 1.0 - (time.monotonic() - started)))

    @property
    def diagnostics(self) -> dict:
        """Expose verified state without implying app or SolarVault acceptance."""
        reading, error = self.snapshot.current()
        return {
            "verification_level": "discovery_only",
            "advertised": self._service is not None,
            "shelly_reading_fresh": reading is not None,
            "shelly_error": error,
            "app_recognized": False,
            "bound": False,
            "live_metering_confirmed": False,
        }

    async def _measurement(self, _request: web.Request) -> web.Response:
        """Keep this path reserved without inventing a 3P response schema."""
        return web.json_response(
            {"status": "unverified", "reason": "3P measurement contract has not been captured"},
            status=503,
            headers={"Cache-Control": "no-store"},
        )
