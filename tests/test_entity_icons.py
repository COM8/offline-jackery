"""Tests for user-facing SolarVault entity icons."""

from custom_components.offline_jackery.sensor import icon_for_path


def test_sensor_icons_match_the_measurement_domain() -> None:
    assert icon_for_path("telemetry.batInPw") == "mdi:battery-plus"
    assert icon_for_path("telemetry.batOutPw") == "mdi:battery-minus"
    assert icon_for_path("telemetry.pv.0.egy") == "mdi:solar-power"
    assert icon_for_path("system.gridInPw") == "mdi:transmission-tower-import"
    assert icon_for_path("system.gridOutPw") == "mdi:transmission-tower-export"
    assert icon_for_path("telemetry.wsig") == "mdi:wifi"


def test_unknown_dynamic_sensor_still_has_a_generic_icon() -> None:
    assert icon_for_path("telemetry.pv.0.commState") == "mdi:information-outline"
    assert icon_for_path("telemetry.unknownReading", "12.5") == "mdi:chart-line"
