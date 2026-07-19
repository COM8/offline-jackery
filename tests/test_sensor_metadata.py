"""Tests for user-facing SolarVault entity metadata."""

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass

from custom_components.offline_jackery.sensor import (
    FIELD_METADATA,
    UNIT_CELSIUS,
    UNIT_DBM,
    UNIT_WATT,
    UNIT_WATT_HOUR,
    friendly_name,
    numeric_value,
    property_description,
    sensor_type_for,
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


def test_phase_energy_is_a_cumulative_energy_sensor_with_a_friendly_name() -> None:
    path = "telemetry.cts.0.aPhaseEgy"
    assert friendly_name(path) == "Cts1 A Phase Energy"
    assert sensor_type_for(path, 1234) == (
        SensorDeviceClass.ENERGY,
        UNIT_WATT_HOUR,
        SensorStateClass.TOTAL_INCREASING,
    )


def test_new_numeric_fields_default_to_measurements() -> None:
    assert sensor_type_for("telemetry.unknownReading", 12.5) == (
        None,
        None,
        SensorStateClass.MEASUREMENT,
    )
    assert sensor_type_for("telemetry.unknownLabel", "test") == (None, None, None)


def test_numeric_protocol_strings_are_published_as_numbers() -> None:
    assert numeric_value("123") == 123
    assert numeric_value("12.5") == 12.5
    assert numeric_value("not a number") == "not a number"
