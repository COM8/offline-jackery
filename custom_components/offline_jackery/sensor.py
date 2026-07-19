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
UNIT_VOLT = "V"
UNIT_AMPERE = "A"
UNIT_HERTZ = "Hz"
NUMERIC_STRING = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")
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
TENTHS_CELSIUS_PATHS = {"telemetry.cellTemp"}
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


def _is_energy_key(key: str) -> bool:
    """Return whether a field is an accumulated-energy counter."""
    normalized = key.lower()
    return normalized in ENERGY_KEYS or normalized.endswith(("egy", "energy"))


def _is_power_key(key: str) -> bool:
    """Return whether a field is an instantaneous power reading."""
    normalized = key.lower()
    return key in POWER_KEYS or normalized.endswith(("pw", "power"))


def numeric_value(value: object | None) -> str | int | float | bool | None:
    """Convert a device's numeric-string field to a Home Assistant number."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not NUMERIC_STRING.fullmatch(text):
        return value
    return float(text) if any(marker in text.lower() for marker in (".", "e")) else int(text)


def native_sensor_value(path: str, value: object | None) -> str | int | float | bool | None:
    """Convert a raw protocol value into its documented engineering unit."""
    converted = numeric_value(value)
    if path in TENTHS_CELSIUS_PATHS and isinstance(converted, (int, float)) and not isinstance(converted, bool):
        return converted / 10
    return converted


def sensor_type_for(path: str, value: object | None = None) -> tuple[SensorDeviceClass | None, str | None, SensorStateClass | None]:
    """Classify a protocol field for Home Assistant statistics and display."""
    value = numeric_value(value)
    key = path.rsplit(".", 1)[-1]
    normalized = key.lower()
    device_class: SensorDeviceClass | None = None
    unit: str | None = None
    state_class: SensorStateClass | None = None
    if _is_energy_key(key):
        device_class, unit, state_class = (
            SensorDeviceClass.ENERGY,
            UNIT_WATT_HOUR,
            SensorStateClass.TOTAL_INCREASING,
        )
    elif _is_power_key(key):
        device_class, unit, state_class = (
            SensorDeviceClass.POWER,
            UNIT_WATT,
            SensorStateClass.MEASUREMENT,
        )
    elif key in PERCENT_KEYS:
        unit, state_class = PERCENTAGE, SensorStateClass.MEASUREMENT
    elif key in TEMPERATURE_KEYS or normalized.endswith("temp"):
        device_class, unit, state_class = (
            SensorDeviceClass.TEMPERATURE,
            UNIT_CELSIUS,
            SensorStateClass.MEASUREMENT,
        )
    elif key in SIGNAL_KEYS:
        device_class, unit, state_class = (
            SensorDeviceClass.SIGNAL_STRENGTH,
            UNIT_DBM,
            SensorStateClass.MEASUREMENT,
        )
    elif normalized.endswith(("vol", "volt", "voltage")):
        device_class, unit, state_class = (
            SensorDeviceClass.VOLTAGE,
            UNIT_VOLT,
            SensorStateClass.MEASUREMENT,
        )
    elif normalized.endswith(("cur", "current", "amp", "ampere")):
        device_class, unit, state_class = (
            SensorDeviceClass.CURRENT,
            UNIT_AMPERE,
            SensorStateClass.MEASUREMENT,
        )
    elif normalized.endswith(("freq", "frequency", "hz")):
        device_class, unit, state_class = (
            SensorDeviceClass.FREQUENCY,
            UNIT_HERTZ,
            SensorStateClass.MEASUREMENT,
        )
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        # Newly discovered numeric protocol fields are measurements by default.
        # This keeps them numeric/statistical instead of treating every update as
        # an untyped state, while named counters above retain their total class.
        state_class = SensorStateClass.MEASUREMENT
    return device_class, unit, state_class


def icon_for_path(path: str, value: object | None = None) -> str:
    """Return an icon appropriate for a protocol field."""
    key = path.rsplit(".", 1)[-1].lower()
    numeric = numeric_value(value)
    icon = ICON_BY_KEY.get(
        key,
        "mdi:chart-line" if isinstance(numeric, (int, float)) and not isinstance(numeric, bool) else "mdi:information-outline",
    )
    if _is_energy_key(key):
        icon = "mdi:solar-power" if ".pv." in path.lower() else "mdi:counter"
    elif key not in ICON_BY_KEY and _is_power_key(key):
        icon = "mdi:flash"
    elif key.endswith(("vol", "volt", "voltage", "freq", "frequency", "hz")):
        icon = "mdi:sine-wave"
    elif key.endswith(("cur", "current", "amp", "ampere")):
        icon = "mdi:current-ac"
    return icon


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
    "telemetry.cellTemp": ("Battery cell temperature", "Battery-cell temperature.", UNIT_CELSIUS),
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
    words = re.sub(r"(?i)(egy|energy)$", "Energy", words).strip() if _is_energy_key(key) else words.replace("Soc", "state of charge").replace("Pv", "PV").strip()
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
    if _is_energy_key(key):
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
        value = native_sensor_value(path, nested_value(coordinator.data, path))
        self._attr_icon = icon_for_path(path, value)
        self._set_unique_id(path.replace(".", "_"))
        device_class, unit, state_class = sensor_type_for(path, value)
        self._attr_device_class = device_class
        self._attr_native_unit_of_measurement = unit
        self._attr_state_class = state_class

    @property
    def native_value(self) -> str | int | float | bool | None:
        """Return the latest value for this protocol path."""
        return native_sensor_value(self.path, nested_value(self.coordinator.data, self.path))

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        """Explain the raw source and meaning of the property."""
        return {
            "protocol_field": self.path,
            "description": property_description(self.path),
        }
