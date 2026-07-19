"""Offline Jackery Home Assistant integration."""

from __future__ import annotations

import voluptuous as vol
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError

# Home Assistant imports custom integration modules before setup in the import
# executor. Import the always-used platforms here so forwarding entry setups does
# not need to load platform modules from disk inside the event loop.
from . import binary_sensor as _binary_sensor  # noqa: F401
from . import button as _button  # noqa: F401
from . import number as _number  # noqa: F401
from . import sensor as _sensor  # noqa: F401
from . import switch as _switch  # noqa: F401
from .bridge import ShellySolarVaultBridge, normalize_serial
from .config_flow import (
    CONF_ADDRESS,
    CONF_ADVERTISE_ADDRESS,
    CONF_BLUETOOTH_KEY,
    CONF_BRIDGE_PORT,
    CONF_BRIDGE_SERIAL,
    CONF_ENTRY_TYPE,
    CONF_INVERT_POWER,
    CONF_SHELLY_HOST,
    CONF_SHELLY_PASSWORD,
    CONF_SHELLY_USERNAME,
    ENTRY_TYPE_BRIDGE,
)
from .const import DOMAIN
from .coordinator import OfflineJackeryDataUpdateCoordinator
from .data import OfflineJackeryConfigEntry, OfflineJackeryData, ShellyBridgeData

PLATFORMS = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.SWITCH,
    Platform.NUMBER,
    Platform.BUTTON,
]

SERVICE_BIND_BRIDGE = "bind_shelly_bridge"


async def async_setup(hass: HomeAssistant, _config: dict) -> bool:
    """Register the explicit local BLE binding action."""

    async def async_bind_bridge(call: ServiceCall) -> None:
        entry = hass.config_entries.async_get_entry(call.data["config_entry_id"])
        if entry is None or not isinstance(entry.runtime_data, OfflineJackeryData):
            raise ServiceValidationError(
                "config_entry_id must identify a loaded Jackery entry"
            )
        serial = normalize_serial(call.data[CONF_BRIDGE_SERIAL])
        configured = any(
            candidate.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_BRIDGE
            and candidate.data.get(CONF_BRIDGE_SERIAL) == serial
            and isinstance(candidate.runtime_data, ShellyBridgeData)
            for candidate in hass.config_entries.async_entries(DOMAIN)
        )
        if not configured:
            message = f"No loaded Shelly bridge has serial {serial}"
            raise ServiceValidationError(message)
        await entry.runtime_data.coordinator.async_bind_local_p1_meter(serial)

    hass.services.async_register(
        DOMAIN,
        SERVICE_BIND_BRIDGE,
        async_bind_bridge,
        schema=vol.Schema(
            {
                vol.Required("config_entry_id"): str,
                vol.Required(CONF_BRIDGE_SERIAL): vol.All(str, normalize_serial),
            }
        ),
    )
    return True


async def async_setup_entry(
    hass: HomeAssistant, entry: OfflineJackeryConfigEntry
) -> bool:
    """Set up one locally connected Jackery device or Shelly bridge."""
    if entry.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_BRIDGE:
        bridge = ShellySolarVaultBridge(
            hass,
            host=entry.data[CONF_SHELLY_HOST],
            serial=entry.data[CONF_BRIDGE_SERIAL],
            port=entry.data[CONF_BRIDGE_PORT],
            advertise_address=entry.data[CONF_ADVERTISE_ADDRESS],
            username=entry.data.get(CONF_SHELLY_USERNAME, "admin"),
            password=entry.data.get(CONF_SHELLY_PASSWORD, ""),
            invert_power=entry.data.get(CONF_INVERT_POWER, False),
        )
        await bridge.async_start()
        entry.runtime_data = ShellyBridgeData(bridge)
        return True

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
    """Unload platforms, Bluetooth, or the local bridge."""
    if isinstance(entry.runtime_data, ShellyBridgeData):
        await entry.runtime_data.bridge.async_stop()
        return True

    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.coordinator.async_shutdown()
    return unloaded
