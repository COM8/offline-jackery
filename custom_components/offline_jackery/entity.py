"""Shared Offline Jackery entity."""

from __future__ import annotations

from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .config_flow import CONF_SERIAL_NUMBER, CONF_SYSTEM_NAME
from .const import DOMAIN, MANUFACTURER, MODEL_SOLARVAULT_3_PRO
from .coordinator import OfflineJackeryDataUpdateCoordinator


class OfflineJackeryEntity(CoordinatorEntity[OfflineJackeryDataUpdateCoordinator]):
    """Base entity tied to one SolarVault coordinator."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: OfflineJackeryDataUpdateCoordinator) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        serial = entry.data[CONF_SERIAL_NUMBER]
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, serial)},
            connections={(CONNECTION_BLUETOOTH, coordinator.address)},
            manufacturer=MANUFACTURER,
            model=MODEL_SOLARVAULT_3_PRO,
            name=entry.data[CONF_SYSTEM_NAME],
            serial_number=serial,
        )

    def _set_unique_id(self, suffix: str) -> None:
        """Use stable serial plus property suffix for entity identity."""

        serial = self.coordinator.config_entry.data[CONF_SERIAL_NUMBER]
        self._attr_unique_id = f"{serial}_{suffix}"
