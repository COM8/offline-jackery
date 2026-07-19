"""Verified writable Boolean SolarVault properties."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import nested_value
from .data import OfflineJackeryConfigEntry
from .entity import OfflineJackeryEntity


async def async_setup_entry(
    _hass: object,
    entry: OfflineJackeryConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        [
            OfflineJackerySwitch(
                coordinator,
                key="eps_output",
                name="Emergency backup output",
                path="telemetry.swEps",
                setter=coordinator.async_set_eps,
            ),
            OfflineJackerySwitch(
                coordinator,
                key="smart_meter_following",
                name="Follow smart meter",
                path="system.isFollowMeterPw",
                setter=coordinator.async_set_follow_meter,
            ),
        ]
    )


class OfflineJackerySwitch(OfflineJackeryEntity, SwitchEntity):
    """A Boolean field with a verified Jackery write command."""

    def __init__(
        self,
        coordinator: Any,
        *,
        key: str,
        name: str,
        path: str,
        setter: Callable[[bool], Awaitable[None]],
    ) -> None:
        super().__init__(coordinator)
        self._attr_name = name
        self._attr_icon = {
            "telemetry.swEps": "mdi:power",
            "system.isFollowMeterPw": "mdi:transmission-tower",
        }[path]
        self._set_unique_id(key)
        self._path = path
        self._setter = setter

    @property
    def is_on(self) -> bool | None:
        """Return the latest confirmed switch state."""
        value = nested_value(self.coordinator.data, self._path)
        return bool(value) if isinstance(value, (bool, int)) else None

    async def async_turn_on(self, **_kwargs: Any) -> None:
        """Enable the field and refresh device state."""
        await self._setter(True)

    async def async_turn_off(self, **_kwargs: Any) -> None:
        """Disable the field and refresh device state."""
        await self._setter(False)

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        """Describe the safety-sensitive writable property."""
        descriptions = {
            "telemetry.swEps": ("Turns the SolarVault emergency backup (EPS) output on or off. This can affect connected equipment."),
            "system.isFollowMeterPw": ("Allows the SolarVault to adjust its output using the configured smart meter."),
        }
        return {"protocol_field": self._path, "description": descriptions[self._path]}
