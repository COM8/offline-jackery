"""Pure tests for reconnect scheduling and telemetry flattening."""

from custom_components.offline_jackery.coordinator import (
    ExponentialBackoff,
    scalar_values,
)


def test_binary_exponential_backoff_caps_at_64_seconds() -> None:
    backoff = ExponentialBackoff()
    assert [backoff.failed(0) for _ in range(9)] == [1, 2, 4, 8, 16, 32, 64, 64, 64]
    assert not backoff.ready(63)
    assert backoff.ready(64)
    backoff.reset()
    assert backoff.failures == 0


def test_scalar_values_includes_nested_objects_and_lists() -> None:
    assert scalar_values({"telemetry": {"pv": [{"power": 123}], "online": True}}) == {
        "telemetry.pv.0.power": 123,
        "telemetry.online": True,
    }
