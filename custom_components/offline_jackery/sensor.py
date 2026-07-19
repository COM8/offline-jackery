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
UNIT_WATT_HOUR = "Wh"
UNIT_DBM = "dBm"
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
SIGNAL_KEYS = {"wsig"}
ENERGY_KEYS = {"egy", "energy", "totalegy", "totalenergy"}

ICON_BY_KEY = {
    "batsoc": "mdi:battery",
    "soc": "mdi:battery",
    "batinpw": "mdi:battery-plus",
    "batoutpw": "mdi:battery-minus",
    "celltemp": "mdi:thermometer",
    "pvpw": "mdi:solar-power",
    "outongridpw": "mdi:transmission-tower-export",
    "inongridpw": "mdi:transmission-tower-import",
    "gridinpw": "mdi:transmission-tower-import",
    "gridoutpw": "mdi:transmission-tower-export",
    "otherloadpw": "mdi:home-lightning-bolt",
    "energyplanpw": "mdi:chart-timeline-variant-shimmer",
    "outgridsidepw": "mdi:transmission-tower-export",
    "ingridsidepw": "mdi:transmission-tower-import",
    "swepsinpw": "mdi:power-plug",
    "swepsoutpw": "mdi:power",
    "standbypw": "mdi:sleep",
    "defaultpw": "mdi:home-lightning-bolt",
    "maxsysoutpw": "mdi:flash",
    "maxsysinpw": "mdi:flash-outline",
    "maxoutpw": "mdi:flash",
    "maxgridstdpw": "mdi:transmission-tower",
    "maxinvstdpw": "mdi:flash-outline",
    "wsig": "mdi:wifi",
}


def icon_for_path(path: str) -> str:
    """Return an icon appropriate for a protocol field."""
    key = path.rsplit(".", 1)[-1].lower()
    if key in ENERGY_KEYS:
        return "mdi:solar-power" if ".pv." in path.lower() else "mdi:counter"
    return ICON_BY_KEY.get(key, "mdi:information-outline")


# Names used by the Jackery app and the decompiled HomeBody/SystemBody models.
# Keep this keyed by the full path: the same protocol field can occur in both
# the device telemetry and the combined-system response with different scope.
FIELD_METADATA = {
    "telemetry.batSoc": ("Battery state of charge", "Current charge level of the SolarVault battery.", None),
    "system.soc": ("System battery state of charge", "Combined battery charge level reported for the SolarVault system.", None),
    "telemetry.batInPw": ("Battery charging power", "Power currently flowing into the SolarVault battery.", UNIT_WATT),
    "telemetry.batOutPw": ("Battery discharging power", "Power currently flowing out of the SolarVault battery.", UNIT_WATT),
    "system.batInPw": ("System battery charging power", "Combined battery charging power reported for the SolarVault system.", UNIT_WATT),
    "system.batOutPw": ("System battery discharging power", "Combined battery discharging power reported for the SolarVault system.", UNIT_WATT),
    "telemetry.cellTemp": ("Battery cell temperature", "Temperature reported by the SolarVault battery cells.", UNIT_CELSIUS),
    "telemetry.pvPw": ("Solar PV power", "Instantaneous photovoltaic power produced by the connected solar inputs.", UNIT_WATT),
    "telemetry.outOngridPw": ("SolarVault on-grid output power", "Power supplied by the SolarVault to the on-grid output; this can include power used by household loads.", UNIT_WATT),
    "telemetry.inOngridPw": ("SolarVault on-grid input power", "Power received by the SolarVault from the on-grid side.", UNIT_WATT),
    "telemetry.swEpsInPw": ("EPS input power", "Power entering the SolarVault emergency-power-supply output path.", UNIT_WATT),
    "telemetry.swEpsOutPw": ("EPS output power", "Power currently supplied through the SolarVault emergency-power-supply output.", UNIT_WATT),
    "system.gridInPw": ("Grid import power", "Power currently imported from the public grid, as measured by the configured meter.", UNIT_WATT),
    "system.gridOutPw": ("Grid export power", "Power currently exported to the public grid, as measured by the configured meter.", UNIT_WATT),
    "system.otherLoadPw": ("Household load power", "Power currently consumed by loads other than the SolarVault system.", UNIT_WATT),
    "system.energyPlanPw": ("Energy plan power", "Power target calculated by the SolarVault energy-management plan.", UNIT_WATT),
    "system.outGridSidePw": ("Grid-side output power", "Power flowing from the SolarVault toward the grid-side output.", UNIT_WATT),
    "system.inGridSidePw": ("Grid-side input power", "Power flowing from the grid-side input toward the SolarVault.", UNIT_WATT),
    "system.swEpsInPw": ("System EPS input power", "Combined-system power entering the emergency-power-supply path.", UNIT_WATT),
    "system.swEpsOutPw": ("System EPS output power", "Combined-system power supplied through the emergency-power-supply output.", UNIT_WATT),
    "system.standbyPw": ("Standby power", "Power reserved or consumed while the SolarVault system is in standby.", UNIT_WATT),
    "system.defaultPw": ("Default household load power", "Configured default household load power used by the SolarVault control model.", UNIT_WATT),
    "system.maxSysOutPw": ("Maximum system output power", "Maximum output power reported by the SolarVault system.", UNIT_WATT),
    "system.maxSysInPw": ("Maximum system input power", "Maximum input power reported by the SolarVault system.", UNIT_WATT),
    "telemetry.maxOutPw": ("Maximum device output power", "Maximum output power reported by the SolarVault device.", UNIT_WATT),
    "telemetry.maxGridStdPw": ("Maximum grid power", "Maximum grid power supported by the SolarVault device.", UNIT_WATT),
    "telemetry.maxInvStdPw": ("Maximum inverter power", "Maximum inverter power supported by the SolarVault device.", UNIT_WATT),
    "telemetry.socChgLimit": ("Battery charge limit", "Configured maximum battery state of charge.", None),
    "telemetry.socDischgLimit": ("Battery discharge limit", "Configured minimum battery state of charge for discharge protection.", None),
    "telemetry.wsig": ("Wi-Fi signal strength", "Wi-Fi signal strength reported by the SolarVault network interface.", UNIT_DBM),
}


def friendly_name(path: str) -> str:
    """Turn a protocol path into a stable, readable entity name."""
    metadata = FIELD_METADATA.get(path)
    if metadata:
        return metadata[0]
    parts = path.split(".")
    key = parts[-1]
    words = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", key).replace("Pw", " power")
    words = "energy" if key.lower() in ENERGY_KEYS else words.replace("Soc", "state of charge").replace("Pv", "PV").strip()
    prefix = " / ".join("PV" if part.lower() == "pv" else part.title() for part in parts[1:-1] if not part.isdigit())
    index = next((str(int(part) + 1) for part in parts if part.isdigit()), "")
    name = words[0].upper() + words[1:] if words else key
    label_prefix = f"{prefix}{index}" if prefix and index else " ".join(item for item in (prefix, index) if item)
    return " ".join(item for item in (label_prefix, name) if item)


def property_description(path: str) -> str:
    """Provide a clear source description for diagnostics and users."""
    metadata = FIELD_METADATA.get(path)
    if metadata:
        return metadata[1]
    key = path.rsplit(".", 1)[-1]
    if key.lower() in ENERGY_KEYS:
        return "Cumulative energy reported by this SolarVault input. The value is recorded as an increasing energy total in watt-hours."
    known = {}
    return known.get(key, f"Read-only `{path}` value reported by the Jackery BLE interface.")


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
        paths = {path: value for path, value in scalar_values(coordinator.data or {}).items() if not isinstance(value, bool)}
        new_paths = sorted(set(paths) - known - CONTROL_PATHS)
        if new_paths:
            known.update(new_paths)
            async_add_entities(OfflineJackerySensor(coordinator, path) for path in new_paths)

    add_missing()
    entry.async_on_unload(coordinator.async_add_listener(add_missing))


class OfflineJackerySensor(OfflineJackeryEntity, SensorEntity):
    """One dynamically discovered, read-only BLE property."""

    def __init__(self, coordinator: Any, path: str) -> None:
        super().__init__(coordinator)
        self.path = path
        self._attr_name = friendly_name(path)
        self._attr_icon = icon_for_path(path)
        self._set_unique_id(path.replace(".", "_"))
        key = path.rsplit(".", 1)[-1]
        if key.lower() in ENERGY_KEYS:
            self._attr_device_class = SensorDeviceClass.ENERGY
            self._attr_native_unit_of_measurement = UNIT_WATT_HOUR
            self._attr_state_class = SensorStateClass.TOTAL_INCREASING
        elif key in POWER_KEYS:
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
        elif key in SIGNAL_KEYS:
            self._attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
            self._attr_native_unit_of_measurement = UNIT_DBM
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
