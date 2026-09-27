"""Tests for the emulated HomeWizard P1 discovery contract."""

import asyncio
from types import SimpleNamespace

from custom_components.offline_jackery import async_migrate_entry
from custom_components.offline_jackery.bridge import (
    HOMEWIZARD_API_PORT,
    homewizard_json_response,
    homewizard_measurement,
    homewizard_service_info,
    shelly_rpc_url,
)


def test_homewizard_service_info_matches_api_v1_discovery() -> None:
    service = homewizard_service_info(
        serial="AABBCCDDEEFF",
        address="192.0.2.10",
        port=HOMEWIZARD_API_PORT,
    )

    assert service.name == "p1meter-DDEEFF._hwenergy._tcp.local."
    assert service.server == "p1meter-ddeeff.local."
    assert service.port == 80
    assert service.parsed_addresses() == ["192.0.2.10"]
    assert service.properties == {
        b"api_enabled": b"1",
        b"path": b"/api/v1",
        b"serial": b"AABBCCDDEEFF",
        b"product_name": b"P1 Meter",
        b"product_type": b"HWE-P1",
    }


def test_migration_moves_old_default_bridge_to_homewizard_port() -> None:
    updates: list[dict[str, object]] = []
    hass = SimpleNamespace(config_entries=SimpleNamespace(async_update_entry=lambda _entry, **changes: updates.append(changes)))
    entry = SimpleNamespace(
        version=1,
        data={"entry_type": "shelly_bridge", "bridge_port": 21001},
    )

    assert asyncio.run(async_migrate_entry(hass, entry))
    assert updates == [
        {
            "data": {"entry_type": "shelly_bridge", "bridge_port": 80},
            "version": 3,
        }
    ]


def test_measurement_does_not_claim_meter_unique_id() -> None:
    measurement = homewizard_measurement(
        {
            "total_act_power": 12.3,
            "a_act_power": 10.0,
            "a_voltage": 230.0,
            "a_current": 0.1,
            "b_act_power": 2.0,
            "b_voltage": 231.0,
            "b_current": 0.2,
            "c_act_power": 0.3,
            "c_voltage": 232.0,
            "c_current": 0.3,
            "total_act": 123456.0,
            "total_act_ret": 7890.0,
        },
    )

    assert "unique_id" not in measurement
    assert measurement["meter_model"] == "Shelly Pro 3EM"
    assert measurement["total_power_import_kwh"] == 123.456
    assert measurement["total_power_import_t1_kwh"] == 123.456
    assert measurement["total_power_import_t2_kwh"] == 0
    assert measurement["total_power_export_kwh"] == 7.89
    assert measurement["total_power_export_t1_kwh"] == 7.89
    assert measurement["total_power_export_t2_kwh"] == 0


def test_shelly_rpc_url_supports_energy_counters() -> None:
    assert shelly_rpc_url("192.0.2.10", method="EMData.GetStatus") == ("http://192.0.2.10/rpc/EMData.GetStatus?id=0")


def test_homewizard_json_response_uses_embedded_device_content_type() -> None:
    response = homewizard_json_response({"active_power_w": 12.3})

    assert response.headers["Content-Type"] == "application/json"
    assert response.body == b'{"active_power_w":12.3}'
