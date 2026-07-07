"""Offline Jackery Home Assistant integration."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .config_flow import CONF_ADDRESS, CONF_BLUETOOTH_KEY
from .coordinator import OfflineJackeryDataUpdateCoordinator
from .data import OfflineJackeryConfigEntry, OfflineJackeryData

# Home Assistant imports custom integration modules before setup in the import
# executor. Import the always-used platforms here so forwarding entry setups does
# not need to load platform modules from disk inside the event loop.
from . import binary_sensor as _binary_sensor  # noqa: F401, E402
from . import button as _button  # noqa: F401, E402
from . import number as _number  # noqa: F401, E402
from . import sensor as _sensor  # noqa: F401, E402
from . import switch as _switch  # noqa: F401, E402

PLATFORMS = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.SWITCH,
    Platform.NUMBER,
    Platform.BUTTON,
]


async def async_setup_entry(
    hass: HomeAssistant, entry: OfflineJackeryConfigEntry
) -> bool:
    """Set up one locally connected Jackery device."""

    coordinator = OfflineJackeryDataUpdateCoordinator(
        hass,
        config_entry=entry,
        address=entry.data[CONF_ADDRESS],
        bluetooth_key=entry.data[CONF_BLUETOOTH_KEY],
    )
    entry.runtime_data = OfflineJackeryData(coordinator)
    await coordinator.async_config_entry_first_refresh()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: OfflineJackeryConfigEntry
) -> bool:
    """Unload platforms and close Bluetooth."""

    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.coordinator.async_shutdown()
    return unloaded
