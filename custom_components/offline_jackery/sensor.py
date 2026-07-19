"""Read-only entities for every scalar property returned over BLE."""

from __future__ import annotations

import re
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE
from homeassistant.core import callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import nested_value, scalar_values
from .data import OfflineJackeryConfigEntry
from .entity import OfflineJackeryEntity

CONTROL_PATHS = {"telemetry.swEps", "system.isFollowMeterPw", "system.maxFeedGrid"}
UNIT_CELSIUS = "°C"
UNIT_WATT = "W"
POWER_KEYS = {
    "batInPw",
    "batOutPw",
    "defaultPw",
    "energyPlanPw",
    "gridInPw",
    "gridOutPw",
    "inGridSidePw",
    "inOngridPw",
    "maxGridStdPw",
    "maxInvStdPw",
    "maxOutPw",
    "maxSysInPw",
    "maxSysOutPw",
    "otherLoadPw",
    "outGridSidePw",
    "outOngridPw",
    "pvPw",
    "standbyPw",
    "swEpsInPw",
    "swEpsOutPw",
}
PERCENT_KEYS = {"batSoc", "soc", "socChgLimit", "socDischgLimit"}
TEMPERATURE_KEYS = {"cellTemp"}


def friendly_name(path: str) -> str:
    """Turn a protocol path into a stable, readable entity name."""
    parts = path.split(".")
    key = parts[-1]
    words = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", key).replace("Pw", " power")
    words = words.replace("Soc", "state of charge").replace("Pv", "PV")
    prefix = " / ".join(part.title() for part in parts[1:-1] if not part.isdigit())
    index = next((str(int(part) + 1) for part in parts if part.isdigit()), "")
    name = words[0].upper() + words[1:] if words else key
    return " ".join(item for item in (prefix, index, name) if item)


def property_description(path: str) -> str:
    """Provide a clear source description for diagnostics and users."""
    key = path.rsplit(".", 1)[-1]
    known = {
        "batSoc": "Battery state of charge reported by the SolarVault.",
        "soc": "Combined system battery state of charge.",
        "gridInPw": (
            "Power currently imported from the public grid as measured by the meter."
        ),
        "gridOutPw": (
            "Power currently exported to the public grid as measured by the meter."
        ),
        "outOngridPw": "SolarVault on-grid output, including power used by local loads.",
        "pvPw": "Combined instantaneous photovoltaic input power.",
        "cellTemp": "Battery cell temperature reported by the SolarVault.",
        "wsig": "Wi-Fi signal value reported by the device.",
    }
    return known.get(
        key, f"Read-only `{path}` value reported by the Jackery BLE interface."
    )


async def async_setup_entry(
    _hass: Any,
    entry: OfflineJackeryConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add sensors now and whenever a later snapshot introduces properties."""
    coordinator = entry.runtime_data.coordinator
    known: set[str] = set()

    @callback
    def add_missing() -> None:
        paths = {
            path: value
            for path, value in scalar_values(coordinator.data or {}).items()
            if not isinstance(value, bool)
        }
        new_paths = sorted(set(paths) - known - CONTROL_PATHS)
        if new_paths:
            known.update(new_paths)
            async_add_entities(
                OfflineJackerySensor(coordinator, path) for path in new_paths
            )

    add_missing()
    entry.async_on_unload(coordinator.async_add_listener(add_missing))


class OfflineJackerySensor(OfflineJackeryEntity, SensorEntity):
    """One dynamically discovered, read-only BLE property."""

    def __init__(self, coordinator: Any, path: str) -> None:
        super().__init__(coordinator)
        self.path = path
        self._attr_name = friendly_name(path)
        self._set_unique_id(path.replace(".", "_"))
        key = path.rsplit(".", 1)[-1]
        if key in POWER_KEYS:
            self._attr_device_class = SensorDeviceClass.POWER
            self._attr_native_unit_of_measurement = UNIT_WATT
            self._attr_state_class = SensorStateClass.MEASUREMENT
        elif key in PERCENT_KEYS:
            self._attr_native_unit_of_measurement = PERCENTAGE
            self._attr_state_class = SensorStateClass.MEASUREMENT
        elif key in TEMPERATURE_KEYS:
            self._attr_device_class = SensorDeviceClass.TEMPERATURE
            self._attr_native_unit_of_measurement = UNIT_CELSIUS
            self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> str | int | float | bool | None:
        """Return the latest value for this protocol path."""
        return nested_value(self.coordinator.data, self.path)

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        """Explain the raw source and meaning of the property."""
        return {
            "protocol_field": self.path,
            "description": property_description(self.path),
        }
