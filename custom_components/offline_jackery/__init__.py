"""Offline Jackery Home Assistant integration."""

from __future__ import annotations

import secrets

import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

# Home Assistant imports custom integration modules before setup in the import
# executor. Import the always-used platforms here so forwarding entry setups does
# not need to load platform modules from disk inside the event loop.
from . import binary_sensor as _binary_sensor  # noqa: F401
from . import button as _button  # noqa: F401
from . import number as _number  # noqa: F401
from . import select as _select  # noqa: F401
from . import sensor as _sensor  # noqa: F401
from . import switch as _switch  # noqa: F401
from .api import JackeryApiError, JackeryCloudClient
from .bridge import HOMEWIZARD_API_PORT, ShellySolarVaultBridge, normalize_serial
from .config_flow import (
    CONF_3P_BIND_KEY,
    CONF_ACCOUNT,
    CONF_ADDRESS,
    CONF_ADVERTISE_ADDRESS,
    CONF_BLUETOOTH_KEY,
    CONF_BRIDGE_PORT,
    CONF_BRIDGE_PROTOCOL,
    CONF_BRIDGE_SERIAL,
    CONF_ENTRY_TYPE,
    CONF_INVERT_POWER,
    CONF_LOGIN_METHOD,
    CONF_REGION,
    CONF_SERIAL_NUMBER,
    CONF_SHELLY_AUTH,
    CONF_SHELLY_HOST,
    CONF_SHELLY_PASSWORD,
    CONF_SHELLY_USERNAME,
    ENTRY_TYPE_BRIDGE,
    ENTRY_VERSION,
    PROTOCOL_HOMEWIZARD_P1,
    PROTOCOL_JACKERY_3P,
    REGION_CODE_LENGTH,
)
from .const import DOMAIN, LOGGER
from .coordinator import OfflineJackeryDataUpdateCoordinator
from .data import OfflineJackeryConfigEntry, OfflineJackeryData, ShellyBridgeData
from .jackery_3p import Jackery3PDiscoveryBridge, normalize_bind_key

PLATFORMS = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.SWITCH,
    Platform.NUMBER,
    Platform.BUTTON,
    Platform.SELECT,
]

SERVICE_BIND_BRIDGE = "bind_shelly_bridge"
SERVICE_FIRMWARE_URLS = "show_firmware_download_urls"
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
LEGACY_BRIDGE_PORT = 21001


async def async_setup(hass: HomeAssistant, _config: dict) -> bool:
    """Register the explicit local BLE binding action."""

    async def async_bind_bridge(call: ServiceCall) -> None:
        entry = hass.config_entries.async_get_entry(call.data["config_entry_id"])
        if entry is None or entry.state is not ConfigEntryState.LOADED or not isinstance(entry.runtime_data, OfflineJackeryData):
            raise ServiceValidationError("config_entry_id must identify a loaded Jackery entry")
        serial = normalize_serial(call.data[CONF_BRIDGE_SERIAL])
        configured = any(
            candidate.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_BRIDGE
            and candidate.data.get(CONF_BRIDGE_PROTOCOL, PROTOCOL_HOMEWIZARD_P1) == PROTOCOL_HOMEWIZARD_P1
            and candidate.data.get(CONF_BRIDGE_SERIAL) == serial
            and candidate.state is ConfigEntryState.LOADED
            for candidate in hass.config_entries.async_entries(DOMAIN)
        )
        if not configured:
            message = f"No loaded Shelly bridge has serial {serial}"
            raise ServiceValidationError(message)
        await entry.runtime_data.coordinator.async_select_meter_bridge(serial)

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

    async def async_show_firmware_urls(call: ServiceCall) -> ServiceResponse:
        entry = hass.config_entries.async_get_entry(call.data["config_entry_id"])
        if entry is None or entry.state is not ConfigEntryState.LOADED or not isinstance(entry.runtime_data, OfflineJackeryData):
            raise ServiceValidationError("config_entry_id must identify a loaded Jackery SolarVault entry")
        serial_number = entry.data.get(CONF_SERIAL_NUMBER)
        if not isinstance(serial_number, str) or not serial_number:
            raise ServiceValidationError("The selected Jackery entry has no serial number")
        method = call.data[CONF_LOGIN_METHOD]
        region = call.data.get(CONF_REGION, "").strip().upper()
        if method == "email" and len(region) != REGION_CODE_LENGTH:
            raise ServiceValidationError("Email login requires a two-letter region code")
        client = JackeryCloudClient(async_get_clientsession(hass))
        try:
            systems = await client.async_login(
                account=call.data[CONF_ACCOUNT].strip() if method == "email" else None,
                phone=call.data[CONF_ACCOUNT].strip() if method == "phone" else None,
                password=call.data["password"],
                region_code=region if method == "email" else None,
            )
            if not any(system.serial_number == serial_number for system in systems):
                raise ServiceValidationError("The Jackery account does not contain the selected SolarVault")
            return await client.async_firmware_urls(serial_number)
        except JackeryApiError as err:
            raise ServiceValidationError(str(err)) from err

    hass.services.async_register(
        DOMAIN,
        SERVICE_FIRMWARE_URLS,
        async_show_firmware_urls,
        schema=vol.Schema({
            vol.Required("config_entry_id"): str,
            vol.Required(CONF_LOGIN_METHOD): vol.In(["email", "phone"]),
            vol.Required(CONF_ACCOUNT): vol.All(str, vol.Length(min=1)),
            vol.Required("password"): vol.All(str, vol.Length(min=1)),
            vol.Optional(CONF_REGION): str,
        }),
        supports_response=SupportsResponse.ONLY,
    )
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: OfflineJackeryConfigEntry) -> bool:
    """Preserve P1 settings and replace unparseable provisional 3P keys."""
    if entry.version >= ENTRY_VERSION:
        return True

    data = dict(entry.data)
    if entry.version == 1 and data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_BRIDGE and data.get(CONF_BRIDGE_PORT) == LEGACY_BRIDGE_PORT:
        data[CONF_BRIDGE_PORT] = HOMEWIZARD_API_PORT
    if data.get(CONF_BRIDGE_PROTOCOL) == PROTOCOL_JACKERY_3P:
        try:
            data[CONF_3P_BIND_KEY] = normalize_bind_key(data[CONF_3P_BIND_KEY])
        except ValueError:
            data[CONF_3P_BIND_KEY] = f"{secrets.randbelow(0x80000000):08X}"
            LOGGER.warning("Regenerated a 3P discovery key that the Jackery app cannot parse")
    hass.config_entries.async_update_entry(entry, data=data, version=ENTRY_VERSION)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: OfflineJackeryConfigEntry) -> bool:
    """Set up one locally connected Jackery device or Shelly bridge."""
    if entry.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_BRIDGE:
        common = {
            "hass": hass,
            "host": entry.data[CONF_SHELLY_HOST],
            "serial": entry.data[CONF_BRIDGE_SERIAL],
            "port": entry.data[CONF_BRIDGE_PORT],
            "advertise_address": entry.data[CONF_ADVERTISE_ADDRESS],
            "username": entry.data.get(CONF_SHELLY_USERNAME, "admin"),
            "password": (
                entry.data.get(CONF_SHELLY_PASSWORD, "")
                if entry.data.get(
                    CONF_SHELLY_AUTH,
                    bool(entry.data.get(CONF_SHELLY_PASSWORD)),
                )
                else ""
            ),
        }
        protocol = entry.data.get(CONF_BRIDGE_PROTOCOL, PROTOCOL_HOMEWIZARD_P1)
        if protocol == PROTOCOL_JACKERY_3P:
            bridge = Jackery3PDiscoveryBridge(**common, bind_key=entry.data[CONF_3P_BIND_KEY])
        elif protocol == PROTOCOL_HOMEWIZARD_P1:
            bridge = ShellySolarVaultBridge(**common, invert_power=entry.data.get(CONF_INVERT_POWER, False))
        else:
            message = f"Unknown bridge protocol: {protocol}"
            raise ValueError(message)
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


async def async_unload_entry(hass: HomeAssistant, entry: OfflineJackeryConfigEntry) -> bool:
    """Unload platforms, Bluetooth, or the local bridge."""
    if isinstance(entry.runtime_data, ShellyBridgeData):
        await entry.runtime_data.bridge.async_stop()
        return True

    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.coordinator.async_shutdown()
    return unloaded
