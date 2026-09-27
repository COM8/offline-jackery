"""Config entry diagnostics without account or Shelly credentials."""

from __future__ import annotations

from homeassistant.core import HomeAssistant

from .data import OfflineJackeryConfigEntry, ShellyBridgeData
from .jackery_3p import Jackery3PDiscoveryBridge


async def async_get_config_entry_diagnostics(_hass: HomeAssistant, entry: OfflineJackeryConfigEntry) -> dict:
    """Report only observed 3P verification state."""
    runtime = entry.runtime_data
    if isinstance(runtime, ShellyBridgeData) and isinstance(runtime.bridge, Jackery3PDiscoveryBridge):
        return runtime.bridge.diagnostics
    return {"verification_level": "not_applicable"}
