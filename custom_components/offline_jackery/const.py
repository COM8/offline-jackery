"""Constants for Offline Jackery."""

import logging

DOMAIN = "offline_jackery"
MANUFACTURER = "Jackery"
MODEL_SOLARVAULT_3_PRO = "SolarVault 3 Pro"
UPDATE_INTERVAL_SECONDS = 5
MAX_RETRY_INTERVAL_SECONDS = 64
CONF_SELECTED_BRIDGE_SERIAL = "selected_bridge_serial"

LOGGER = logging.getLogger(__package__)
