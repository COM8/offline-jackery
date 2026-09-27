"""Shared Shelly Pro 3EM acquisition and freshness tracking."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from aiohttp import ClientError, ClientSession, ClientTimeout, DigestAuthMiddleware
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

SHELLY_RPC_METHODS = {"EM.GetStatus", "EMData.GetStatus"}
STALE_SECONDS = 5.0


class BridgeError(RuntimeError):
    """The Shelly response or bridge configuration is unusable."""


def shelly_rpc_url(host: str, *, method: str = "EM.GetStatus") -> str:
    """Build a Gen2 RPC URL from a host or base URL."""
    if method not in SHELLY_RPC_METHODS:
        raise ValueError("Unsupported Shelly RPC method")
    raw = host.strip()
    if "://" not in raw:
        raw = f"http://{raw}"
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Enter a valid Shelly hostname, IP address, or HTTP URL")
    return urlunsplit((parsed.scheme, parsed.netloc, f"/rpc/{method}", "id=0", ""))


def required_number(source: dict[str, Any], key: str) -> float:
    """Reject absent or nonnumeric fields, including booleans."""
    value = source.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        message = f"Shelly response is missing numeric field {key!r}"
        raise BridgeError(message)
    return float(value)


def validate_measurement(value: dict[str, Any]) -> None:
    """Require the shared phase and energy fields before publishing a reading."""
    for key in ("total_act_power", "total_act", "total_act_ret"):
        required_number(value, key)
    for phase in "abc":
        for suffix in ("act_power", "voltage", "current"):
            required_number(value, f"{phase}_{suffix}")


@dataclass(slots=True)
class ShellySnapshot:
    measurement: dict[str, Any] | None = None
    updated: float = 0.0
    error: str = "Waiting for the first Shelly reading"

    def record(self, value: dict[str, Any]) -> None:
        validate_measurement(value)
        self.measurement = dict(value)
        self.updated = time.monotonic()
        self.error = ""

    def current(self) -> tuple[dict[str, Any] | None, str]:
        age = time.monotonic() - self.updated
        if self.measurement is None:
            return None, self.error
        if age > STALE_SECONDS:
            return None, f"Meter data is stale ({age:.1f}s): {self.error or 'no response'}"
        return dict(self.measurement), self.error


class ShellyReader:
    """Read the two RPC responses with optional Shelly digest authentication."""

    def __init__(self, hass: HomeAssistant, host: str, username: str, password: str) -> None:
        self.hass = hass
        self.urls = (shelly_rpc_url(host), shelly_rpc_url(host, method="EMData.GetStatus"))
        self.username = username
        self.password = password
        self.session: ClientSession | None = None

    def start(self) -> None:
        if self.password:
            self.session = ClientSession(middlewares=(DigestAuthMiddleware(self.username, self.password),))

    async def close(self) -> None:
        if self.session is not None:
            await self.session.close()
            self.session = None

    async def read(self) -> dict[str, Any]:
        temporary: ClientSession | None = None
        if self.session is not None:
            session = self.session
        elif self.password:
            temporary = ClientSession(middlewares=(DigestAuthMiddleware(self.username, self.password),))
            session = temporary
        else:
            session = async_get_clientsession(self.hass)
        value: dict[str, Any] = {}
        try:
            for url in self.urls:
                async with session.get(url, timeout=ClientTimeout(total=2)) as response:
                    response.raise_for_status()
                    payload = await response.json(content_type=None)
                if not isinstance(payload, dict):
                    raise BridgeError("Shelly returned a non-object JSON value")
                value.update(payload)
            validate_measurement(value)
        except (TimeoutError, ClientError, ValueError) as err:
            message = f"Shelly request failed: {err}"
            raise BridgeError(message) from err
        finally:
            if temporary is not None:
                await temporary.close()
        return value
