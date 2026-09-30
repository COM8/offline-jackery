"""Tests for Jackery setup API helpers."""

import asyncio
from typing import Any

import pytest

from custom_components.offline_jackery.api import JackeryCloudClient, JackerySystem, normalize_systems


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


def test_firmware_urls_uses_version_ids_without_starting_update(monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[tuple[str, str, dict[str, str] | None]] = []

    async def request(_self: JackeryCloudClient, method: str, path: str, fields: dict[str, str] | None = None) -> Any:
        requests.append((method, path, fields))
        if path == "device/ota/list":
            return [{"deviceSn": "OTHER"}, {
                "deviceSn": "SV123",
                "currentVersion": "1.0",
                "targetVersion": "1.1",
                "targetVersionId": "42",
                "targetModuleVersion": ["11", "12"],
                "updateStatus": 0,
            }]
        return {"MAIN": "https://example.com/main.bin", "BMS": "https://example.com/bms.bin", "deviceSn": "SV123"}

    monkeypatch.setattr(JackeryCloudClient, "_request", request)
    result = asyncio.run(JackeryCloudClient(None).async_firmware_urls("SV123"))

    assert result == {
        "device_sn": "SV123",
        "current_version": "1.0",
        "target_version": "1.1",
        "update_status": 0,
        "urls": {"BMS": "https://example.com/bms.bin", "MAIN": "https://example.com/main.bin"},
    }
    assert requests == [
        ("GET", "device/ota/list", {"deviceSnList": "SV123"}),
        ("POST", "device/ota/bluetooth", {
            "deviceSn": "SV123",
            "subDeviceSn": "",
            "targetFirmwareIds": "11,12",
            "targetVersionId": "42",
        }),
    ]


def test_firmware_urls_returns_empty_when_no_target_modules(monkeypatch: pytest.MonkeyPatch) -> None:
    async def request(_self: JackeryCloudClient, method: str, path: str, fields: dict[str, str] | None = None) -> Any:
        assert method == "GET"
        assert path == "device/ota/list"
        assert fields == {"deviceSnList": "SV123"}
        return [{"deviceSn": "SV123", "currentVersion": "1.0", "updateStatus": 6, "targetModuleVersion": []}]

    monkeypatch.setattr(JackeryCloudClient, "_request", request)
    result = asyncio.run(JackeryCloudClient(None).async_firmware_urls("SV123"))
    assert result["urls"] == {}
