"""Configuration wizard for Offline Jackery."""

from __future__ import annotations

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
from .const import DOMAIN, LOGGER
from .protocol import JACKERY_SERVICE_UUID, ProtocolError, decode_bluetooth_key

CONF_ADDRESS = "address"
CONF_BLUETOOTH_KEY = "bluetooth_key"
CONF_LOGIN_METHOD = "login_method"
CONF_ACCOUNT = "account"
CONF_REGION = "region"
CONF_RESCAN = "rescan"
CONF_SERIAL_NUMBER = "serial_number"
CONF_SYSTEM_NAME = "system_name"

VALIDATION_EXCEPTIONS = (
    BleakError,
    ConnectionError,
    TimeoutError,
    RuntimeError,
    ProtocolError,
)


class OfflineJackeryFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Guide account login, system choice, discovery, and validation."""

    VERSION = 1

    def __init__(self) -> None:
        self._cloud: JackeryCloudClient | None = None
        self._systems: dict[str, JackerySystem] = {}
        self._system: JackerySystem | None = None
        self._key = ""
        self._address = ""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Authenticate without persisting the account credentials."""

        errors: dict[str, str] = {}
        if user_input is not None:
            method = user_input[CONF_LOGIN_METHOD]
            account = user_input[CONF_ACCOUNT].strip()
            region = user_input.get(CONF_REGION, "").strip().upper()
            if method == "email" and len(region) != 2:
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
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_LOGIN_METHOD, default="email"
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=["email", "phone"],
                            translation_key="login_method",
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    vol.Required(CONF_ACCOUNT): selector.TextSelector(),
                    vol.Required("password"): selector.TextSelector(
                        selector.TextSelectorConfig(
                            type=selector.TextSelectorType.PASSWORD
                        )
                    ),
                    vol.Optional(CONF_REGION): selector.TextSelector(),
                }
            ),
            errors=errors,
        )

    async def async_step_system(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Select one account system and obtain its Bluetooth key."""

        errors: dict[str, str] = {}
        if user_input is not None:
            self._system = self._systems[user_input[CONF_SERIAL_NUMBER]]
            assert self._cloud is not None
            try:
                self._key = await self._cloud.async_bluetooth_key(self._system)
                decode_bluetooth_key(self._key)
            except (JackeryApiError, ProtocolError):
                LOGGER.exception("Could not obtain a valid Jackery Bluetooth key")
                errors["base"] = "key_failed"
            else:
                await self.async_set_unique_id(self._system.serial_number)
                self._abort_if_unique_id_configured()
                return await self.async_step_bluetooth()

        options = [
            selector.SelectOptionDict(
                value=serial,
                label=f"{system.name} — {serial}"
                + (" — SolarVault 3 Pro" if system.model_code == 3001 else ""),
            )
            for serial, system in self._systems.items()
        ]
        return self.async_show_form(
            step_id="system",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SERIAL_NUMBER): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=options)
                    )
                }
            ),
            errors=errors,
        )

    async def async_step_bluetooth(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Actively scan and allow only a serial-matching Jackery device."""

        assert self._system is not None
        errors: dict[str, str] = {}
        if bluetooth.async_scanner_count(self.hass, connectable=True) == 0:
            return self.async_abort(reason="no_bluetooth_adapter")
        if user_input is not None:
            if user_input.get(CONF_RESCAN):
                return await self.async_step_bluetooth()
            self._address = user_input[CONF_ADDRESS]
            return await self.async_step_validate()

        await bluetooth.async_request_active_scan(self.hass)
        discoveries = bluetooth.async_discovered_service_info(
            self.hass, connectable=True
        )
        matching: list[selector.SelectOptionDict] = []
        other_jackery: list[str] = []
        other_ble = 0
        for info in discoveries:
            jackery = is_jackery(list(info.service_uuids))
            serial = advertised_serial(dict(info.manufacturer_data))
            label = f"{info.name or 'Unnamed'} — {info.address}"
            if serial:
                label += f" — serial {serial}"
            if jackery and serial_matches(
                self._system.serial_number, serial, info.name
            ):
                matching.append(
                    selector.SelectOptionDict(
                        value=info.address, label=f"✓ Match — {label}"
                    )
                )
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
                    "other_jackery": "\n".join(
                        f"• {item}" for item in other_jackery[:8]
                    )
                    or "None",
                    "other_ble_count": str(other_ble),
                },
            )
        details = "\n".join(f"• {item}" for item in other_jackery[:8]) or "None"
        return self.async_show_form(
            step_id="bluetooth",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ADDRESS): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=matching)
                    )
                }
            ),
            description_placeholders={
                "serial": self._system.serial_number,
                "other_jackery": details,
                "other_ble_count": str(other_ble),
            },
            errors=errors,
        )

    async def async_step_bluetooth_empty(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Route menu choices when no matching Bluetooth device was visible."""

        del user_input
        return await self.async_step_bluetooth()

    async def async_step_validate(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Connect with the key and read the first complete status snapshot."""

        del user_input
        device = bluetooth.async_ble_device_from_address(
            self.hass, self._address, connectable=True
        )
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
                reason=(
                    "The Bluetooth connection opened, but the first status read failed."
                ),
                details=str(err) or err.__class__.__name__,
            )
        await client.async_disconnect()
        return await self.async_step_confirm()

    def _show_validation_menu(
        self, *, reason: str, details: str
    ) -> config_entries.ConfigFlowResult:
        """Show actionable retry choices after connect or read validation fails."""

        return self.async_show_menu(
            step_id="validate",
            menu_options=["validate", "bluetooth"],
            description_placeholders={
                "reason": reason,
                "details": details,
            },
        )

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Show validation success and create the entry on Add."""

        assert self._system is not None
        del user_input
        return self.async_show_menu(
            step_id="confirm",
            menu_options=["create", "bluetooth"],
            description_placeholders={"name": self._system.name},
        )

    async def async_step_create(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Create the config entry after the user confirms the validated device."""

        assert self._system is not None
        del user_input
        return self.async_create_entry(
            title=self._system.name,
            data={
                CONF_ADDRESS: self._address,
                CONF_BLUETOOTH_KEY: self._key,
                CONF_SERIAL_NUMBER: self._system.serial_number,
                CONF_SYSTEM_NAME: self._system.name,
            },
        )
