"""Custom types for offline_jackery."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.loader import Integration

    from .api import IntegrationOfflineJackeryApiClient
    from .coordinator import OfflineJackeryDataUpdateCoordinator


type IntegrationOfflineJackeryConfigEntry = ConfigEntry[IntegrationOfflineJackeryData]


@dataclass
class IntegrationOfflineJackeryData:
    """Data for the OfflineJackery integration."""

    client: IntegrationOfflineJackeryApiClient
    coordinator: OfflineJackeryDataUpdateCoordinator
    integration: Integration
