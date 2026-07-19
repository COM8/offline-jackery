"""Connectivity and read-only Boolean properties for Offline Jackery."""

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import nested_value, scalar_values
from .data import OfflineJackeryConfigEntry
from .entity import OfflineJackeryEntity
from .sensor import friendly_name, property_description


async def async_setup_entry(
    _hass: object,
    entry: OfflineJackeryConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up connectivity and dynamic Boolean properties."""
    coordinator = entry.runtime_data.coordinator
    async_add_entities([OfflineJackeryConnectivity(coordinator)])
    known: set[str] = set()

    @callback
    def add_missing() -> None:
        boolean_paths = {path for path, value in scalar_values(coordinator.data or {}).items() if isinstance(value, bool)}
        new_paths = sorted(boolean_paths - known)
        if new_paths:
            known.update(new_paths)
            async_add_entities(OfflineJackeryBooleanSensor(coordinator, path) for path in new_paths)

    add_missing()
    entry.async_on_unload(coordinator.async_add_listener(add_missing))


class OfflineJackeryConnectivity(OfflineJackeryEntity, BinarySensorEntity):
    """Whether Home Assistant currently has an active GATT link."""

    _attr_name = "Bluetooth connection"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_icon = "mdi:bluetooth-connect"

    def __init__(self, coordinator: object) -> None:
        super().__init__(coordinator)
        self._set_unique_id("bluetooth_connection")

    @property
    def available(self) -> bool:
        """Keep connectivity visible while the device is disconnected."""
        return True

    @property
    def is_on(self) -> bool:
        """Return whether a GATT connection is active."""
        return self.coordinator.connected

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        """Explain the connectivity state."""
        return {"description": "Shows whether Home Assistant currently has an active Bluetooth connection to the SolarVault."}


class OfflineJackeryBooleanSensor(OfflineJackeryEntity, BinarySensorEntity):
    """One read-only Boolean returned by the BLE interface."""

    def __init__(self, coordinator: Any, path: str) -> None:
        super().__init__(coordinator)
        self.path = path
        self._attr_name = friendly_name(path)
        self._attr_icon = "mdi:toggle-switch-outline"
        self._set_unique_id(path.replace(".", "_"))

    @property
    def is_on(self) -> bool | None:
        """Return the latest Boolean value."""
        value = nested_value(self.coordinator.data, self.path)
        return value if isinstance(value, bool) else None

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        """Explain the raw source and meaning of the property."""
        return {
            "protocol_field": self.path,
            "description": property_description(self.path),
        }
