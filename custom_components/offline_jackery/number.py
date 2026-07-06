"""Verified writable numeric SolarVault properties."""

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import UnitOfElectricPower
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import nested_value
from .data import OfflineJackeryConfigEntry
from .entity import OfflineJackeryEntity


async def async_setup_entry(
    _hass: object,
    entry: OfflineJackeryConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the verified feed-in ceiling control."""

    async_add_entities([OfflineJackeryFeedGridLimit(entry.runtime_data.coordinator)])


class OfflineJackeryFeedGridLimit(OfflineJackeryEntity, NumberEntity):
    """Maximum grid feed-in power accepted by the SolarVault."""

    _attr_name = "Maximum grid feed-in power"
    _attr_icon = "mdi:transmission-tower-export"
    _attr_native_min_value = 0
    _attr_native_step = 10
    _attr_native_unit_of_measurement = UnitOfElectricPower.WATT
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator: object) -> None:
        super().__init__(coordinator)
        self._set_unique_id("maximum_grid_feed_in_power")

    @property
    def native_value(self) -> float | None:
        """Return the confirmed configured ceiling."""

        value = nested_value(self.coordinator.data, "system.maxFeedGrid")
        return float(value) if isinstance(value, (int, float)) else None

    @property
    def native_max_value(self) -> float:
        """Use the maximum output reported by this device."""

        value = nested_value(self.coordinator.data, "system.maxSysOutPw")
        return float(value) if isinstance(value, (int, float)) and value > 0 else 0

    async def async_set_native_value(self, value: float) -> None:
        """Set a validated feed-in ceiling."""

        await self.coordinator.async_set_feed_grid_limit(int(value))

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        """Describe what the grid limit does and does not control."""

        return {
            "protocol_field": "system.maxFeedGrid",
            "description": (
                "Grid export ceiling in watts. Actual export also depends on PV, "
                "battery, household load, meter following, and firmware limits."
            ),
        }
