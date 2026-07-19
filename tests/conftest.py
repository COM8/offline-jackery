"""Keep pure unit tests independent of Home Assistant runtime integrations."""

from __future__ import annotations

import sys
from types import ModuleType

# Importing Home Assistant's Bluetooth integration also imports USB transport
# backends whose dependencies are installed by Home Assistant at runtime. None
# of these unit tests exercise the Home Assistant Bluetooth facade.
sys.modules.setdefault(
    "homeassistant.components.bluetooth",
    ModuleType("homeassistant.components.bluetooth"),
)
