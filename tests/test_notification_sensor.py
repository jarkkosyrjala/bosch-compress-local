"""Tests for honest Bosch notification-buffer presentation."""

from __future__ import annotations

from typing import Any, cast

from custom_components.bosch_compress.models import BoschEndpoint
from custom_components.bosch_compress.runtime import BoschRuntimeData
from custom_components.bosch_compress.sensor import BoschEndpointSensor

_PATH = "/notifications"


class _FakeCoordinator:
    """Provide one mutable endpoint snapshot to a sensor entity."""

    def __init__(self, value: list[dict[str, Any]]) -> None:
        self.last_update_success = True
        self.discovered_endpoints = {_PATH: self._endpoint(value)}

    @staticmethod
    def _endpoint(value: list[dict[str, Any]]) -> BoschEndpoint:
        return BoschEndpoint(
            path=_PATH,
            value=value,
            value_type="errorList",
            writable=False,
        )

    def endpoint(self, path: str) -> BoschEndpoint | None:
        """Return the current notification endpoint."""
        return self.discovered_endpoints.get(path)

    def replace(self, value: list[dict[str, Any]]) -> None:
        """Replace the notification buffer without changing its length."""
        self.discovered_endpoints[_PATH] = self._endpoint(value)


def _sensor(
    value: list[dict[str, Any]],
) -> tuple[BoschEndpointSensor, _FakeCoordinator]:
    """Build a notification sensor and its mutable coordinator."""
    coordinator = _FakeCoordinator(value)
    runtime = BoschRuntimeData(
        coordinator=cast(Any, coordinator),
        gateway_uuid="test-gateway",
        host="192.0.2.1",
    )
    return BoschEndpointSensor(runtime, _PATH), coordinator


def test_notification_state_identifies_newest_stored_record() -> None:
    """Use the newest record identity instead of a non-changing list count."""
    sensor, coordinator = _sensor(
        [
            {"dcd": "A62", "ccd": 1082, "t": "2026-03-01T23:01:16"},
            {"dcd": "A61", "ccd": 1081, "t": "2026-03-01T23:09:38"},
        ]
    )

    assert sensor.native_value == "A61-1081"
    assert sensor.extra_state_attributes["record_count"] == 2
    assert sensor.extra_state_attributes["latest_timestamp"] == ("2026-03-01T23:09:38")
    assert "not current alarm state" in sensor.extra_state_attributes["semantics"]

    coordinator.replace([{"dcd": "A11", "ccd": 3081, "t": "2026-08-28T09:23:43"}])

    assert sensor.native_value == "A11-3081"
    assert sensor.extra_state_attributes["latest_code"] == "A11-3081"


def test_empty_notification_buffer_has_explicit_non_alarm_state() -> None:
    """An empty stored buffer is distinct from an inferred current-alarm bit."""
    sensor, _coordinator = _sensor([])

    assert sensor.native_value == "none"
    assert sensor.extra_state_attributes["record_count"] == 0
