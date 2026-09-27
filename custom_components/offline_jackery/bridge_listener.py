"""Reference-counted HTTP listener with per-path bridge ownership."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from aiohttp import web
from homeassistant.core import HomeAssistant

from .shelly_reader import BridgeError

type Handler = Callable[[web.Request], Awaitable[web.Response]]
REGISTRY_KEY = "offline_jackery_bridge_listeners"


class BridgeListener:
    """One TCP listener shared by bridges with disjoint HTTP paths."""

    def __init__(self, port: int) -> None:
        self.port = port
        self.routes: dict[tuple[str, str], tuple[str, Handler]] = {}
        self.runner: web.AppRunner | None = None
        self._lock = asyncio.Lock()

    async def claim(self, owner: str, routes: dict[tuple[str, str], Handler]) -> None:
        async with self._lock:
            if any(route in self.routes for route in routes):
                raise BridgeError("HTTP route is already owned by another bridge")
            if self.runner is None:
                app = web.Application()
                app.router.add_route("*", "/{path:.*}", self._dispatch)
                runner = web.AppRunner(app, access_log=None)
                await runner.setup()
                try:
                    await web.TCPSite(runner, "0.0.0.0", self.port).start()
                except Exception:
                    await runner.cleanup()
                    raise
                self.runner = runner
            self.routes.update({route: (owner, handler) for route, handler in routes.items()})

    async def release(self, owner: str) -> None:
        async with self._lock:
            self.routes = {route: claim for route, claim in self.routes.items() if claim[0] != owner}
            if not self.routes and self.runner is not None:
                await self.runner.cleanup()
                self.runner = None

    async def _dispatch(self, request: web.Request) -> web.Response:
        claim = self.routes.get((request.method, request.path))
        if claim is None:
            raise web.HTTPNotFound
        return await claim[1](request)


def listener_for(hass: HomeAssistant, port: int) -> BridgeListener:
    """Get the shared listener for a Home Assistant host and TCP port."""
    registry = hass.data.setdefault(REGISTRY_KEY, {})
    return registry.setdefault(port, BridgeListener(port))
