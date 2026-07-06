"""Tests for Jackery setup API helpers."""

from custom_components.offline_jackery.api import JackerySystem, normalize_systems


def test_normalize_solarvault_system() -> None:
    systems = normalize_systems(
        [
            {
                "systemName": "Garage",
                "deviceSn": "SV123",
                "bluetoothKey": "secret",
                "devices": [{"deviceId": "device-1", "deviceSn": "SV123", "modelCode": 3001}],
            }
        ]
    )

    assert systems == [JackerySystem("Garage", "SV123", 3001, "device-1", "secret")]


def test_normalize_ignores_entries_without_serial() -> None:
    assert normalize_systems([{"systemName": "Incomplete"}, None]) == []
