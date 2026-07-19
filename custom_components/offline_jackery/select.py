"""Web UI selection of a SolarVault smart-meter source."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .config_flow import (
    CONF_BRIDGE_SERIAL,
    CONF_ENTRY_TYPE,
    ENTRY_TYPE_BRIDGE,
)
from .const import CONF_SELECTED_BRIDGE_SERIAL, DOMAIN
from .coordinator import OfflineJackeryDataUpdateCoordinator
from .data import OfflineJackeryConfigEntry
from .entity import OfflineJackeryEntity

CURRENT_CONFIGURATION = "Current meter configuration (for example online Shelly)"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OfflineJackeryConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add the smart-meter source selector."""
    async_add_entities([OfflineJackeryMeterSourceSelect(hass, entry.runtime_data.coordinator)])


class OfflineJackeryMeterSourceSelect(OfflineJackeryEntity, SelectEntity):
    """Choose one configured local bridge or retain other meter bindings."""

    _attr_name = "Smart-meter source"
    _attr_icon = "mdi:meter-electric"

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: OfflineJackeryDataUpdateCoordinator,
    ) -> None:
        super().__init__(coordinator)
        self._hass = hass
        self._set_unique_id("smart_meter_source")

    def _bridges(self) -> dict[str, str]:
        """Return display label to serial mappings for loaded bridges."""
        bridges: dict[str, str] = {}
        for entry in self._hass.config_entries.async_entries(DOMAIN):
            if entry.data.get(CONF_ENTRY_TYPE) != ENTRY_TYPE_BRIDGE or entry.state is not ConfigEntryState.LOADED:
                continue
            serial = entry.data[CONF_BRIDGE_SERIAL]
            label = f"{entry.title} · {serial[-6:]}"
            bridges[label] = serial
        return bridges

    @property
    def options(self) -> list[str]:
        """List the fallback and every currently loaded local bridge."""
        return [CURRENT_CONFIGURATION, *self._bridges()]

    @property
    def current_option(self) -> str:
        """Return the selected bridge label or the non-local fallback."""
        selected = self.coordinator.config_entry.options.get(CONF_SELECTED_BRIDGE_SERIAL)
        if selected:
            for label, serial in self._bridges().items():
                if serial == selected:
                    return label
        return CURRENT_CONFIGURATION

    async def async_select_option(self, option: str) -> None:
        """Apply the selected source and enable meter following."""
        bridges = self._bridges()
        if option != CURRENT_CONFIGURATION and option not in bridges:
            message: str = f"Unknown or unloaded local bridge: {option}"
            raise ValueError(message)
        await self.coordinator.async_select_meter_bridge(bridges.get(option))
