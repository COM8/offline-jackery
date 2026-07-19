"""Runtime types for Offline Jackery."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

    from .bridge import ShellySolarVaultBridge
    from .coordinator import OfflineJackeryDataUpdateCoordinator

type OfflineJackeryConfigEntry = ConfigEntry[OfflineJackeryData]


@dataclass(slots=True)
class OfflineJackeryData:
    """Runtime resources owned by one config entry."""

    coordinator: OfflineJackeryDataUpdateCoordinator


@dataclass(slots=True)
class ShellyBridgeData:
    """Runtime resources owned by a bridge config entry."""

    bridge: ShellySolarVaultBridge
