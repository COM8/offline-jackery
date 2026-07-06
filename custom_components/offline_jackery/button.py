"""Manual refresh action for Offline Jackery."""

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .data import OfflineJackeryConfigEntry
from .entity import OfflineJackeryEntity


async def async_setup_entry(
    _hass: object,
    entry: OfflineJackeryConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the immediate local refresh action."""

    async_add_entities([OfflineJackeryRefreshButton(entry.runtime_data.coordinator)])


class OfflineJackeryRefreshButton(OfflineJackeryEntity, ButtonEntity):
    """Request an immediate status read, bypassing reconnect backoff."""

    _attr_name = "Refresh status"
    _attr_device_class = ButtonDeviceClass.UPDATE

    def __init__(self, coordinator: object) -> None:
        super().__init__(coordinator)
        self._set_unique_id("refresh_status")

    async def async_press(self) -> None:
        """Bypass reconnect backoff and request status now."""

        await self.coordinator.async_force_refresh()
