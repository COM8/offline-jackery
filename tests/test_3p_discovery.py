"""Discovery-only 3P and shared P1/3P runtime contracts."""

import asyncio
import json
from types import SimpleNamespace
from typing import Self

import pytest
from _pytest.monkeypatch import MonkeyPatch
from aiohttp import ClientError, web
from homeassistant.config_entries import ConfigEntryState

from custom_components.offline_jackery import async_setup_entry
from custom_components.offline_jackery.bridge import homewizard_measurement
from custom_components.offline_jackery.bridge_listener import BridgeListener
from custom_components.offline_jackery.config_flow import (
    CONF_3P_BIND_KEY,
    CONF_ADVERTISE_ADDRESS,
    CONF_BRIDGE_PORT,
    CONF_BRIDGE_SERIAL,
    CONF_ENTRY_TYPE,
    CONF_SHELLY_AUTH,
    CONF_SHELLY_HOST,
    CONF_SHELLY_USERNAME,
    ENTRY_TYPE_BRIDGE,
    OfflineJackeryFlowHandler,
)
from custom_components.offline_jackery.jackery_3p import (
    Jackery3PDiscoveryBridge,
    jackery_3p_service_info,
    normalize_3p_serial,
    normalize_bind_key,
)
from custom_components.offline_jackery.select import OfflineJackeryMeterSourceSelect
from custom_components.offline_jackery.shelly_reader import BridgeError, ShellyReader, ShellySnapshot, validate_measurement


def reading(power: float) -> dict:
    return {
        "total_act_power": power,
        "total_act": 123456.0,
        "total_act_ret": 7890.0,
        "a_act_power": power + 10,
        "a_voltage": 230.0,
        "a_current": 0.1,
        "b_act_power": -10.0,
        "b_voltage": 231.0,
        "b_current": 0.2,
        "c_act_power": 0.0,
        "c_voltage": 232.0,
        "c_current": 0.3,
    }


@pytest.mark.parametrize("power", [300.0, -300.0])
def test_shared_snapshot_preserves_p1_import_export(power: float) -> None:
    snapshot = ShellySnapshot()
    snapshot.record(reading(power))
    current, error = snapshot.current()
    assert error == ""
    assert homewizard_measurement(current)["active_power_w"] == power
    snapshot.updated -= 6
    assert snapshot.current()[0] is None
    assert "stale" in snapshot.current()[1]


def test_missing_shelly_field_is_rejected() -> None:
    value = reading(10)
    del value["c_voltage"]
    with pytest.raises(BridgeError, match="c_voltage"):
        validate_measurement(value)


def test_3p_dns_sd_shape_and_validation() -> None:
    service = jackery_3p_service_info(
        serial="abc123def456", bind_key="0123ABCD", address="192.0.2.10", port=80
    )
    instance = service.name.removesuffix("._jackery_power._tcp.local.")
    parts = instance.split("-")
    assert parts[1] == "ABC123DEF456"
    assert int(parts[2], 16) == 0x0123ABCD
    assert service.server == "jackery3p-abc123def456-0123abcd.local."
    assert service.parsed_addresses() == ["192.0.2.10"]
    assert service.port == 80
    assert service.properties == {}
    with pytest.raises(ValueError, match="serial"):
        normalize_3p_serial("invalid-serial")
    with pytest.raises(ValueError, match="bind key"):
        normalize_bind_key("not-hex")


def test_listener_owns_routes_independently(monkeypatch: MonkeyPatch) -> None:
    started = []
    stopped = []

    async def cleanup(_self: web.AppRunner) -> None:
        stopped.append("cleanup")

    class FakeSite:
        def __init__(self, *_args: object) -> None:
            pass

        async def start(self) -> None:
            started.append("start")

    monkeypatch.setattr(web.AppRunner, "cleanup", cleanup)
    monkeypatch.setattr(web, "TCPSite", FakeSite)

    async def handler(_request: web.Request) -> web.Response:
        return web.json_response({"ok": True})

    async def exercise() -> None:
        listener = BridgeListener(80)
        await listener.claim("p1", {("GET", "/api/v1/data"): handler})
        await listener.claim("3p", {("GET", "/api/measurement"): handler})
        assert started.count("start") == 1
        with pytest.raises(BridgeError, match="already owned"):
            await listener.claim("other-3p", {("GET", "/api/measurement"): handler})
        await listener.release("p1")
        assert ("GET", "/api/measurement") in listener.routes
        assert stopped == []
        await listener.release("3p")
        assert stopped == ["cleanup"]

    asyncio.run(exercise())


def test_3p_diagnostics_and_reserved_route_are_discovery_only() -> None:
    hass = SimpleNamespace(data={})
    bridge = Jackery3PDiscoveryBridge(
        hass, host="192.0.2.20", serial="ABC123DEF456", bind_key="0123ABCD",
        advertise_address="192.0.2.10", port=80,
    )
    assert bridge.diagnostics["verification_level"] == "discovery_only"
    assert bridge.diagnostics["app_recognized"] is False
    assert bridge.diagnostics["bound"] is False
    response = asyncio.run(bridge._measurement(None))  # noqa: SLF001
    assert response.status == 503
    assert json.loads(response.body)["status"] == "unverified"


def test_p1_selector_excludes_3p_mock() -> None:
    p1 = SimpleNamespace(
        data={CONF_ENTRY_TYPE: ENTRY_TYPE_BRIDGE, CONF_BRIDGE_SERIAL: "AABBCCDDEEFF"},
        state=ConfigEntryState.LOADED,
        title="P1 bridge",
    )
    three_phase = SimpleNamespace(
        data={CONF_ENTRY_TYPE: ENTRY_TYPE_BRIDGE, "bridge_protocol": "jackery_3p", CONF_BRIDGE_SERIAL: "ABC123DEF456"},
        state=ConfigEntryState.LOADED,
        title="3P discovery mock",
    )
    selector = SimpleNamespace(_hass=SimpleNamespace(config_entries=SimpleNamespace(async_entries=lambda _domain: [p1, three_phase])))
    assert OfflineJackeryMeterSourceSelect._bridges(selector) == {"P1 bridge · DDEEFF": "AABBCCDDEEFF"}  # noqa: SLF001


def test_3p_advertisement_registers_and_withdraws(monkeypatch: MonkeyPatch) -> None:
    events: list[str] = []

    class FakeZeroconf:
        async def async_register_service(self, _service: object) -> None:
            events.append("register")

        async def async_unregister_service(self, _service: object) -> None:
            events.append("unregister")

    async def get_instance(_hass: object) -> FakeZeroconf:
        return FakeZeroconf()

    async def fake_claim(_self: BridgeListener, _owner: str, _routes: dict) -> None:
        events.append("claim")

    async def fake_release(_self: BridgeListener, _owner: str) -> None:
        events.append("release")

    monkeypatch.setattr("custom_components.offline_jackery.jackery_3p.zeroconf.async_get_async_instance", get_instance)
    monkeypatch.setattr(BridgeListener, "claim", fake_claim)
    monkeypatch.setattr(BridgeListener, "release", fake_release)

    async def exercise() -> None:
        hass = SimpleNamespace(data={}, async_create_background_task=lambda coro, _name: asyncio.create_task(coro))
        bridge = Jackery3PDiscoveryBridge(
            hass, host="192.0.2.20", serial="ABC123DEF456", bind_key="0123ABCD",
            advertise_address="192.0.2.10", port=80,
        )
        await bridge.async_start()
        assert bridge.diagnostics["advertised"] is True
        await bridge.async_stop()
        assert bridge.diagnostics["advertised"] is False

    asyncio.run(exercise())
    assert events == ["claim", "register", "unregister", "release"]


def test_3p_flow_coexists_with_p1_and_rejects_bad_identity(monkeypatch: MonkeyPatch) -> None:
    async def read(_self: Jackery3PDiscoveryBridge) -> dict:
        return reading(10)

    async def executor(_fn: object, _port: int) -> bool:
        return True

    async def set_id(_value: str) -> None:
        return None

    monkeypatch.setattr(Jackery3PDiscoveryBridge, "async_read_shelly", read)
    flow = OfflineJackeryFlowHandler()
    flow.hass = SimpleNamespace(data={}, async_add_executor_job=executor)
    flow.async_set_unique_id = set_id
    flow._abort_if_unique_id_configured = lambda: None  # noqa: SLF001
    flow._async_current_entries = lambda: [SimpleNamespace(data={CONF_ENTRY_TYPE: ENTRY_TYPE_BRIDGE, CONF_BRIDGE_PORT: 80})]  # noqa: SLF001
    flow.async_create_entry = lambda **kwargs: kwargs
    flow.async_show_form = lambda **kwargs: kwargs
    values = {
        CONF_SHELLY_HOST: "192.0.2.20",
        CONF_BRIDGE_SERIAL: "ABC123DEF456",
        CONF_3P_BIND_KEY: "0123ABCD",
        CONF_BRIDGE_PORT: 80,
        CONF_ADVERTISE_ADDRESS: "192.0.2.10",
        CONF_SHELLY_AUTH: False,
        CONF_SHELLY_USERNAME: "admin",
    }
    result = asyncio.run(flow.async_step_jackery_3p_bridge(values))
    assert result["title"].startswith("Jackery Smart Meter 3P discovery mock")
    assert result["data"][CONF_3P_BIND_KEY] == "0123ABCD"
    bad = asyncio.run(flow.async_step_jackery_3p_bridge({**values, CONF_BRIDGE_SERIAL: "bad-serial"}))
    assert bad["errors"]["base"] == "invalid_identity"
    bad_key = asyncio.run(flow.async_step_jackery_3p_bridge({**values, CONF_3P_BIND_KEY: "XYZ"}))
    assert bad_key["errors"]["base"] == "invalid_bind_key"
    bad_address = asyncio.run(flow.async_step_jackery_3p_bridge({**values, CONF_ADVERTISE_ADDRESS: "bad-address"}))
    assert bad_address["errors"]["base"] == "invalid_address"
    flow._async_current_entries = lambda: [SimpleNamespace(data={  # noqa: SLF001
        CONF_ENTRY_TYPE: ENTRY_TYPE_BRIDGE, CONF_BRIDGE_PORT: 80, "bridge_protocol": "jackery_3p"
    })]
    duplicate = asyncio.run(flow.async_step_jackery_3p_bridge(values))
    assert duplicate["errors"]["base"] == "port_in_use"
    flow._async_current_entries = list  # noqa: SLF001

    async def unavailable(_fn: object, _port: int) -> bool:
        return False

    flow.hass.async_add_executor_job = unavailable
    blocked = asyncio.run(flow.async_step_jackery_3p_bridge(values))
    assert blocked["errors"]["base"] == "listener_unavailable"


def test_version_2_p1_entry_defaults_to_p1_protocol(monkeypatch: MonkeyPatch) -> None:
    created: list[dict] = []

    class FakeP1Bridge:
        def __init__(self, **kwargs: object) -> None:
            created.append(kwargs)

        async def async_start(self) -> None:
            pass

    monkeypatch.setattr("custom_components.offline_jackery.ShellySolarVaultBridge", FakeP1Bridge)
    entry = SimpleNamespace(
        version=2,
        data={
            CONF_ENTRY_TYPE: ENTRY_TYPE_BRIDGE,
            CONF_SHELLY_HOST: "192.0.2.20",
            CONF_BRIDGE_SERIAL: "AABBCCDDEEFF",
            CONF_BRIDGE_PORT: 80,
            CONF_ADVERTISE_ADDRESS: "192.0.2.10",
        },
        runtime_data=None,
    )
    assert asyncio.run(async_setup_entry(SimpleNamespace(), entry)) is True
    assert len(created) == 1
    assert created[0]["serial"] == "AABBCCDDEEFF"


def test_shared_reader_uses_digest_auth_and_validates_both_rpc_responses(monkeypatch: MonkeyPatch) -> None:
    requests: list[str] = []
    sessions: list[object] = []

    class FakeResponse:
        def __init__(self, payload: dict) -> None:
            self.payload = payload

        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *_args: object) -> None:
            pass

        def raise_for_status(self) -> None:
            pass

        async def json(self, **_kwargs: object) -> dict:
            return self.payload

    class FakeSession:
        def __init__(self, **kwargs: object) -> None:
            sessions.append(kwargs)

        def get(self, url: str, **_kwargs: object) -> FakeResponse:
            requests.append(url)
            return FakeResponse(reading(-20) if "EM.GetStatus" in url else {"total_act": 100, "total_act_ret": 200})

        async def close(self) -> None:
            pass

    monkeypatch.setattr("custom_components.offline_jackery.shelly_reader.ClientSession", FakeSession)

    async def exercise() -> dict:
        reader = ShellyReader(SimpleNamespace(), "192.0.2.20", "admin", "password")
        result = await reader.read()
        await reader.close()
        return result

    result = asyncio.run(exercise())
    assert len(sessions) == 1
    assert sessions[0]["middlewares"]
    assert requests == [
        "http://192.0.2.20/rpc/EM.GetStatus?id=0",
        "http://192.0.2.20/rpc/EMData.GetStatus?id=0",
    ]
    assert homewizard_measurement(result)["active_power_w"] == -20


def test_shared_reader_reports_authentication_failure(monkeypatch: MonkeyPatch) -> None:
    class UnauthorizedSession:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def get(self, _url: str, **_kwargs: object) -> object:
            raise ClientError("401 Unauthorized")

        async def close(self) -> None:
            pass

    monkeypatch.setattr("custom_components.offline_jackery.shelly_reader.ClientSession", UnauthorizedSession)

    async def exercise() -> None:
        reader = ShellyReader(SimpleNamespace(), "192.0.2.20", "admin", "bad-password")
        with pytest.raises(BridgeError, match="401 Unauthorized"):
            await reader.read()

    asyncio.run(exercise())
