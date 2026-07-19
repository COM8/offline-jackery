"""Tests for user-facing SolarVault entity metadata."""

from custom_components.offline_jackery.sensor import (
    FIELD_METADATA,
    UNIT_CELSIUS,
    UNIT_DBM,
    UNIT_WATT,
    UNIT_WATT_HOUR,
    friendly_name,
    property_description,
)


def test_known_fields_have_friendly_names_and_descriptions() -> None:
    for path, (name, description, _unit) in FIELD_METADATA.items():
        assert friendly_name(path) == name
        assert property_description(path) == description
        assert name
        assert description


def test_known_units_match_the_reverse_engineered_field_types() -> None:
    assert FIELD_METADATA["telemetry.batInPw"][2] == UNIT_WATT
    assert FIELD_METADATA["system.gridOutPw"][2] == UNIT_WATT
    assert FIELD_METADATA["telemetry.cellTemp"][2] == UNIT_CELSIUS
    assert FIELD_METADATA["telemetry.wsig"][2] == UNIT_DBM
    assert FIELD_METADATA["telemetry.batSoc"][2] is None


def test_pv_energy_is_a_cumulative_energy_sensor() -> None:
    path = "telemetry.pv.0.egy"
    assert friendly_name(path) == "PV1 Energy"
    assert property_description(path).startswith("Cumulative energy")
    assert UNIT_WATT_HOUR == "Wh"
