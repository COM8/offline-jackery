"""Tests for Jackery advertisement identification."""

from custom_components.offline_jackery.bluetooth import (
    advertised_serial,
    binding_failures,
    serial_matches,
)


def test_advertised_serial_reconstructs_manufacturer_prefix() -> None:
    # Little-endian 0x5302 => category 2 followed by ASCII "S".
    assert advertised_serial({0x5302: b"V123"}) == "SV123"


def test_advertised_serial_ignores_non_jackery_category() -> None:
    assert advertised_serial({0x5301: b"V123"}) is None


def test_serial_match_is_exact_or_name_fallback() -> None:
    assert serial_matches("SV123", "sv123", None)
    assert serial_matches("SV123", None, "Jackery-SV123")
    assert not serial_matches("SV123", "SV1234", "Jackery")


def test_existing_smart_meter_binding_is_idempotent() -> None:
    assert binding_failures([{"deviceSn": "CCCF653ED203", "code": -2}], "CCCF653ED203") == []


def test_other_smart_meter_binding_errors_are_reported() -> None:
    result = [{"deviceSn": "CCCF653ED203", "code": -3}]
    assert binding_failures(result, "CCCF653ED203") == result
