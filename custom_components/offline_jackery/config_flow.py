"""Configuration wizard for Offline Jackery."""

from __future__ import annotations

import ipaddress
import secrets
import socket
from typing import Any

import voluptuous as vol
from bleak.exc import BleakError
from homeassistant import config_entries
from homeassistant.components import bluetooth
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    JackeryApiError,
    JackeryAuthenticationError,
    JackeryCloudClient,
    JackeryConnectionError,
    JackerySystem,
)
from .bluetooth import SolarVaultClient, advertised_serial, is_jackery, serial_matches
from .bridge import (
    HOMEWIZARD_API_PORT,
    BridgeError,
    ShellySolarVaultBridge,
    homewizard_measurement,
    normalize_serial,
)
from .bridge_listener import listener_for
from .const import DOMAIN, LOGGER
from .jackery_3p import Jackery3PDiscoveryBridge, normalize_3p_serial, normalize_bind_key
from .protocol import ProtocolError, decode_bluetooth_key

CONF_ADDRESS = "address"
CONF_BLUETOOTH_KEY = "bluetooth_key"
CONF_LOGIN_METHOD = "login_method"
CONF_ACCOUNT = "account"
CONF_REGION = "region"
CONF_RESCAN = "rescan"
CONF_SERIAL_NUMBER = "serial_number"
CONF_SYSTEM_NAME = "system_name"
CONF_ENTRY_TYPE = "entry_type"
ENTRY_TYPE_JACKERY = "jackery"
ENTRY_TYPE_BRIDGE = "shelly_bridge"
CONF_SHELLY_HOST = "shelly_host"
CONF_BRIDGE_SERIAL = "bridge_serial"
CONF_BRIDGE_PORT = "bridge_port"
CONF_ADVERTISE_ADDRESS = "advertise_address"
CONF_SHELLY_AUTH = "shelly_authentication"
CONF_SHELLY_USERNAME = "shelly_username"
CONF_SHELLY_PASSWORD = "shelly_password"  # noqa: S105
CONF_INVERT_POWER = "invert_power"
CONF_BRIDGE_PROTOCOL = "bridge_protocol"
PROTOCOL_HOMEWIZARD_P1 = "homewizard_p1"
PROTOCOL_JACKERY_3P = "jackery_3p"
CONF_3P_BIND_KEY = "jackery_3p_bind_key"
REGION_CODE_LENGTH = 2
BRIDGE_PORT_MINIMUM = 1
BRIDGE_PORT_MAXIMUM = 65535
SOLARVAULT_MODEL_CODE = 3001

VALIDATION_EXCEPTIONS = (
    BleakError,
    ConnectionError,
    TimeoutError,
    RuntimeError,
    ProtocolError,
)


def _validate_bridge_port(port: int) -> None:
    """Validate the local bridge listener port."""
    if not BRIDGE_PORT_MINIMUM <= port <= BRIDGE_PORT_MAXIMUM:
        raise ValueError("port")


class BridgeConfigError(ValueError):
    """A config-flow error with a translation key."""


def _check_port_available(port: int) -> bool:
    """Check the actual wildcard listener address used by bridge setup."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("0.0.0.0", port))
    except OSError:
        return False
    return True


async def _ensure_3p_listener_available(hass: Any, port: int) -> None:
    if listener_for(hass, port).runner is None and not await hass.async_add_executor_job(_check_port_available, port):
        raise BridgeConfigError("listener_unavailable")


class OfflineJackeryFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Guide account login, system choice, discovery, and validation."""

    VERSION = 2

    def __init__(self) -> None:
        self._cloud: JackeryCloudClient | None = None
        self._systems: dict[str, JackerySystem] = {}
        self._system: JackerySystem | None = None
        self._key = ""
        self._address = ""

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> config_entries.ConfigFlowResult:
        """Choose a locally controlled Jackery or a meter bridge."""
        del user_input
        return self.async_show_menu(
            step_id="user",
            menu_options={"jackery": "Jackery SolarVault", "shelly_bridge": "Local Shelly Pro 3EM <-> HomeWizard P1 bridge", "jackery_3p_bridge": "Experimental Jackery Smart Meter 3P discovery mock"},
        )

    async def async_step_jackery(self, user_input: dict[str, Any] | None = None) -> config_entries.ConfigFlowResult:
        """Authenticate without persisting the account credentials."""
        errors: dict[str, str] = {}
        if user_input is not None:
            method = user_input[CONF_LOGIN_METHOD]
            account = user_input[CONF_ACCOUNT].strip()
            region = user_input.get(CONF_REGION, "").strip().upper()
            if method == "email" and len(region) != REGION_CODE_LENGTH:
                errors[CONF_REGION] = "invalid_region"
            else:
                self._cloud = JackeryCloudClient(async_get_clientsession(self.hass))
                try:
                    systems = await self._cloud.async_login(
                        account=account if method == "email" else None,
                        phone=account if method == "phone" else None,
                        password=user_input["password"],
                        region_code=region if method == "email" else None,
                    )
                except JackeryAuthenticationError:
                    errors["base"] = "invalid_auth"
                except JackeryConnectionError:
                    errors["base"] = "cannot_connect"
                except JackeryApiError:
                    LOGGER.exception("Jackery account setup failed")
                    errors["base"] = "unknown"
                else:
                    self._systems = {item.serial_number: item for item in systems}
                    if not self._systems:
                        return self.async_abort(reason="no_devices")
                    return await self.async_step_system()

        return self.async_show_form(
            step_id="jackery",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_LOGIN_METHOD, default="email"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=["email", "phone"],
                            translation_key="login_method",
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    vol.Required(CONF_ACCOUNT): selector.TextSelector(),
                    vol.Required("password"): selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)),
                    vol.Optional(CONF_REGION): selector.TextSelector(),
                }
            ),
            errors=errors,
        )

    async def async_step_shelly_bridge(self, user_input: dict[str, Any] | None = None) -> config_entries.ConfigFlowResult:
        """Validate and create one independent Shelly bridge entry."""
        return await self._async_step_bridge(PROTOCOL_HOMEWIZARD_P1, user_input)

    async def async_step_jackery_3p_bridge(self, user_input: dict[str, Any] | None = None) -> config_entries.ConfigFlowResult:
        """Configure a discovery-only 3P mock."""
        return await self._async_step_bridge(PROTOCOL_JACKERY_3P, user_input)

    async def _async_step_bridge(self, protocol: str, user_input: dict[str, Any] | None) -> config_entries.ConfigFlowResult:  # noqa: PLR0912, PLR0915
        errors: dict[str, str] = {}
        suggested_serial = secrets.token_hex(6).upper()
        is_3p = protocol == PROTOCOL_JACKERY_3P
        if user_input is not None:
            try:
                try:
                    serial = normalize_3p_serial(user_input[CONF_BRIDGE_SERIAL]) if is_3p else normalize_serial(user_input[CONF_BRIDGE_SERIAL])
                except ValueError as err:
                    raise BridgeConfigError("invalid_identity") from err
                try:
                    bind_key = normalize_bind_key(user_input[CONF_3P_BIND_KEY]) if is_3p else ""
                except ValueError as err:
                    raise BridgeConfigError("invalid_bind_key") from err
                try:
                    port = int(user_input[CONF_BRIDGE_PORT])
                    _validate_bridge_port(port)
                except ValueError as err:
                    raise BridgeConfigError("invalid_port") from err
                try:
                    ipaddress.IPv4Address(user_input[CONF_ADVERTISE_ADDRESS])
                except ipaddress.AddressValueError as err:
                    raise BridgeConfigError("invalid_address") from err
                common = {
                    "hass": self.hass,
                    "host": user_input[CONF_SHELLY_HOST],
                    "serial": serial,
                    "port": port,
                    "advertise_address": user_input[CONF_ADVERTISE_ADDRESS],
                    "username": user_input[CONF_SHELLY_USERNAME],
                    "password": (user_input.get(CONF_SHELLY_PASSWORD, "") if user_input[CONF_SHELLY_AUTH] else ""),
                }
                bridge = (Jackery3PDiscoveryBridge(**common, bind_key=bind_key) if is_3p
                          else ShellySolarVaultBridge(**common, invert_power=user_input[CONF_INVERT_POWER]))
                reading = await bridge.async_read_shelly()
                if not is_3p:
                    homewizard_measurement(reading, invert_power=user_input[CONF_INVERT_POWER])
                if is_3p:
                    await _ensure_3p_listener_available(self.hass, port)
            except BridgeConfigError as err:
                errors["base"] = str(err)
            except BridgeError, ValueError:
                errors["base"] = "invalid_bridge"
            else:
                await self.async_set_unique_id(f"bridge:{protocol}:{serial}")
                self._abort_if_unique_id_configured()
                for entry in self._async_current_entries():
                    if (entry.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_BRIDGE
                        and entry.data.get(CONF_BRIDGE_PORT) == port
                        and entry.data.get(CONF_BRIDGE_PROTOCOL, PROTOCOL_HOMEWIZARD_P1) == protocol):
                        errors["base"] = "port_in_use"
                        break
                if not errors:
                    data = dict(user_input)
                    data.update(
                        {
                            CONF_ENTRY_TYPE: ENTRY_TYPE_BRIDGE,
                            CONF_BRIDGE_SERIAL: serial,
                            CONF_BRIDGE_PORT: port,
                            CONF_BRIDGE_PROTOCOL: protocol,
                        }
                    )
                    if is_3p:
                        data[CONF_3P_BIND_KEY] = bind_key
                    label = "Jackery Smart Meter 3P discovery mock" if is_3p else "Shelly Pro 3EM <-> HomeWizard P1 bridge"
                    return self.async_create_entry(title=f"{label} {serial[-6:]}", data=data)

        fields: dict = {
            vol.Required(CONF_SHELLY_HOST): selector.TextSelector(),
            vol.Required(CONF_BRIDGE_SERIAL, default=suggested_serial): selector.TextSelector(),
            vol.Required(CONF_BRIDGE_PORT, default=HOMEWIZARD_API_PORT): selector.NumberSelector(
                selector.NumberSelectorConfig(min=1, max=BRIDGE_PORT_MAXIMUM, mode=selector.NumberSelectorMode.BOX)
            ),
            vol.Required(CONF_ADVERTISE_ADDRESS): selector.TextSelector(),
            vol.Required(CONF_SHELLY_AUTH, default=False): selector.BooleanSelector(),
            vol.Required(CONF_SHELLY_USERNAME, default="admin"): selector.TextSelector(),
            vol.Optional(CONF_SHELLY_PASSWORD): selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)),
        }
        if is_3p:
            fields[vol.Required(CONF_3P_BIND_KEY, default=secrets.token_hex(8).upper())] = selector.TextSelector()
        else:
            fields[vol.Required(CONF_INVERT_POWER, default=False)] = selector.BooleanSelector()
        return self.async_show_form(
            step_id="jackery_3p_bridge" if is_3p else "shelly_bridge",
            data_schema=vol.Schema(fields),
            errors=errors,
            description_placeholders={"local_url": "http://shellypro3em.local"},
        )

    async def async_step_system(self, user_input: dict[str, Any] | None = None) -> config_entries.ConfigFlowResult:
        """Select one account system and obtain its Bluetooth key."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._system = self._systems[user_input[CONF_SERIAL_NUMBER]]
            if self._cloud is None:
                raise RuntimeError("Jackery cloud client is not initialized")
            try:
                self._key = await self._cloud.async_bluetooth_key(self._system)
                decode_bluetooth_key(self._key)
            except JackeryApiError, ProtocolError:
                LOGGER.exception("Could not obtain a valid Jackery Bluetooth key")
                errors["base"] = "key_failed"
            else:
                await self.async_set_unique_id(self._system.serial_number)
                self._abort_if_unique_id_configured()
                return await self.async_step_bluetooth()

        options = [
            selector.SelectOptionDict(
                value=serial,
                label=f"{system.name} — {serial}" + (" — SolarVault 3 Pro" if system.model_code == SOLARVAULT_MODEL_CODE else ""),
            )
            for serial, system in self._systems.items()
        ]
        return self.async_show_form(
            step_id="system",
            data_schema=vol.Schema({vol.Required(CONF_SERIAL_NUMBER): selector.SelectSelector(selector.SelectSelectorConfig(options=options))}),
            errors=errors,
        )

    async def async_step_bluetooth(self, user_input: dict[str, Any] | None = None) -> config_entries.ConfigFlowResult:
        """Actively scan and allow only a serial-matching Jackery device."""
        if self._system is None:
            return self.async_abort(reason="invalid_state")
        errors: dict[str, str] = {}
        if bluetooth.async_scanner_count(self.hass, connectable=True) == 0:
            return self.async_abort(reason="no_bluetooth_adapter")
        if user_input is not None:
            if user_input.get(CONF_RESCAN):
                return await self.async_step_bluetooth()
            self._address = user_input[CONF_ADDRESS]
            return await self.async_step_validate()

        await bluetooth.async_request_active_scan(self.hass)
        discoveries = bluetooth.async_discovered_service_info(self.hass, connectable=True)
        matching: list[selector.SelectOptionDict] = []
        other_jackery: list[str] = []
        other_ble = 0
        for info in discoveries:
            jackery = is_jackery(list(info.service_uuids))
            serial = advertised_serial(dict(info.manufacturer_data))
            label = f"{info.name or 'Unnamed'} — {info.address}"
            if serial:
                label += f" — serial {serial}"
            if jackery and serial_matches(self._system.serial_number, serial, info.name):
                matching.append(selector.SelectOptionDict(value=info.address, label=f"✓ Match — {label}"))
            elif jackery:
                other_jackery.append(label)
            else:
                other_ble += 1
        matching.sort(key=lambda item: str(item["label"]))
        if not matching:
            return self.async_show_menu(
                step_id="bluetooth_empty",
                menu_options=["bluetooth", "system"],
                description_placeholders={
                    "serial": self._system.serial_number,
                    "other_jackery": "\n".join(f"• {item}" for item in other_jackery[:8]) or "None",
                    "other_ble_count": str(other_ble),
                },
            )
        details = "\n".join(f"• {item}" for item in other_jackery[:8]) or "None"
        return self.async_show_form(
            step_id="bluetooth",
            data_schema=vol.Schema({vol.Required(CONF_ADDRESS): selector.SelectSelector(selector.SelectSelectorConfig(options=matching))}),
            description_placeholders={
                "serial": self._system.serial_number,
                "other_jackery": details,
                "other_ble_count": str(other_ble),
            },
            errors=errors,
        )

    async def async_step_bluetooth_empty(self, user_input: dict[str, Any] | None = None) -> config_entries.ConfigFlowResult:
        """Route menu choices when no matching Bluetooth device was visible."""
        del user_input
        return await self.async_step_bluetooth()

    async def async_step_validate(self, user_input: dict[str, Any] | None = None) -> config_entries.ConfigFlowResult:
        """Connect with the key and read the first complete status snapshot."""
        del user_input
        device = bluetooth.async_ble_device_from_address(self.hass, self._address, connectable=True)
        if device is None:
            return self.async_abort(reason="device_unavailable")
        client = SolarVaultClient(device, decode_bluetooth_key(self._key))
        try:
            await client.async_connect()
        except VALIDATION_EXCEPTIONS as err:
            LOGGER.exception("Initial Jackery Bluetooth connection failed")
            await client.async_disconnect()
            return self._show_validation_menu(
                reason="The Bluetooth connection could not be opened.",
                details=str(err) or err.__class__.__name__,
            )
        try:
            await client.async_read()
        except VALIDATION_EXCEPTIONS as err:
            LOGGER.exception("Initial Jackery Bluetooth status read failed")
            await client.async_disconnect()
            return self._show_validation_menu(
                reason=("The Bluetooth connection opened, but the first status read failed."),
                details=str(err) or err.__class__.__name__,
            )
        await client.async_disconnect()
        return await self.async_step_confirm()

    def _show_validation_menu(self, *, reason: str, details: str) -> config_entries.ConfigFlowResult:
        """Show actionable retry choices after connect or read validation fails."""
        return self.async_show_menu(
            step_id="validate",
            menu_options=["validate", "bluetooth"],
            description_placeholders={
                "reason": reason,
                "details": details,
            },
        )

    async def async_step_confirm(self, user_input: dict[str, Any] | None = None) -> config_entries.ConfigFlowResult:
        """Show validation success and create the entry on Add."""
        if self._system is None:
            return self.async_abort(reason="invalid_state")
        del user_input
        return self.async_show_menu(
            step_id="confirm",
            menu_options=["create", "bluetooth"],
            description_placeholders={"name": self._system.name},
        )

    async def async_step_create(self, user_input: dict[str, Any] | None = None) -> config_entries.ConfigFlowResult:
        """Create the config entry after the user confirms the validated device."""
        if self._system is None:
            return self.async_abort(reason="invalid_state")
        del user_input
        return self.async_create_entry(
            title=self._system.name,
            data={
                CONF_ENTRY_TYPE: ENTRY_TYPE_JACKERY,
                CONF_ADDRESS: self._address,
                CONF_BLUETOOTH_KEY: self._key,
                CONF_SERIAL_NUMBER: self._system.serial_number,
                CONF_SYSTEM_NAME: self._system.name,
            },
        )
