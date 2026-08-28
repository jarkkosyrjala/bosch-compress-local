"""Tests for Bosch-native heating-circuit target writes."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any, cast

import pytest
from homeassistant.components.climate.const import ClimateEntityFeature
from homeassistant.const import ATTR_TEMPERATURE
from homeassistant.exceptions import HomeAssistantError

from custom_components.bosch_compress.climate import (
    BoschHeatingCircuitClimate,
    _circuit_paths,
)
from custom_components.bosch_compress.models import BoschEndpoint
from custom_components.bosch_compress.runtime import BoschRuntimeData


class _FakeCoordinator:
    """Minimal coordinator exposing endpoint capability metadata."""

    def __init__(
        self, values: dict[str, Any], *, writable_paths: set[str] | None = None
    ) -> None:
        writable_paths = writable_paths or set()
        self.last_update_success = True
        self.discovered_endpoints = {
            path: BoschEndpoint(
                path=path,
                value=value,
                value_type=(
                    "floatValue" if isinstance(value, int | float) else "stringValue"
                ),
                writable=path in writable_paths,
            )
            for path, value in values.items()
        }
        self.writes: list[tuple[str, Any]] = []

    def endpoint(self, path: str) -> BoschEndpoint | None:
        """Return discovered endpoint metadata."""
        return self.discovered_endpoints.get(path)

    async def async_write(self, path: str, value: Any) -> None:
        """Record a native Bosch write."""
        self.writes.append((path, value))

    def set_value(self, path: str, value: Any) -> None:
        """Replace one immutable endpoint snapshot."""
        self.discovered_endpoints[path] = replace(
            self.discovered_endpoints[path], value=value
        )


class _FakeH60:
    """Record fallback writes without an MQTT connection."""

    def __init__(self) -> None:
        self.writes: list[tuple[str, int | float]] = []

    async def async_set_value(self, register: str, value: int | float) -> None:
        """Record a fallback H60 write."""
        self.writes.append((register, value))


def _runtime(
    circuit_id: str,
    operation_mode: str,
    *,
    endpoint_writability: dict[str, bool],
    h60: _FakeH60 | None = None,
) -> tuple[BoschRuntimeData, _FakeCoordinator]:
    """Build runtime data with explicit native endpoint capabilities."""
    base = f"/heatingCircuits/{circuit_id}"
    values: dict[str, Any] = {
        f"{base}/roomtemperature": 21.0,
        f"{base}/currentRoomSetpoint": 21.5,
        f"{base}/operationMode": operation_mode,
    }
    values.update({path: 21.5 for path in endpoint_writability})
    coordinator = _FakeCoordinator(
        values,
        writable_paths={
            path for path, writable in endpoint_writability.items() if writable
        },
    )
    runtime = BoschRuntimeData(
        coordinator=cast(Any, coordinator),
        gateway_uuid="test-gateway",
        host="192.0.2.1",
        h60=cast(Any, h60),
    )
    return runtime, coordinator


@pytest.mark.parametrize("circuit_id", ["hc1", "hc2"])
@pytest.mark.parametrize(
    ("operation_mode", "endpoint_name"),
    [
        ("manual", "manualRoomSetpoint"),
        ("auto", "temporaryRoomSetpoint"),
    ],
)
def test_mode_specific_native_target_takes_precedence_over_h60(
    circuit_id: str, operation_mode: str, endpoint_name: str
) -> None:
    """Each circuit writes the native target matching its operating mode."""
    path = f"/heatingCircuits/{circuit_id}/{endpoint_name}"
    generic_path = f"/heatingCircuits/{circuit_id}/temperatureRoomSetpoint"
    h60 = _FakeH60()
    runtime, coordinator = _runtime(
        circuit_id,
        operation_mode,
        endpoint_writability={path: True, generic_path: True},
        h60=h60,
    )
    entity = BoschHeatingCircuitClimate(runtime, _circuit_paths(circuit_id))

    asyncio.run(entity.async_set_temperature(**{ATTR_TEMPERATURE: 22.5}))

    assert coordinator.writes == [(path, 22.5)]
    assert h60.writes == []


@pytest.mark.parametrize("operation_mode", ["manual", "auto"])
def test_writable_generic_target_is_a_native_fallback(operation_mode: str) -> None:
    """Use the generic Bosch target only when metadata confirms it is writable."""
    path = "/heatingCircuits/hc1/temperatureRoomSetpoint"
    runtime, coordinator = _runtime(
        "hc1", operation_mode, endpoint_writability={path: True}
    )
    entity = BoschHeatingCircuitClimate(runtime, _circuit_paths("hc1"))

    asyncio.run(entity.async_set_temperature(**{ATTR_TEMPERATURE: 20.5}))

    assert coordinator.writes == [(path, 20.5)]
    assert entity.supported_features & ClimateEntityFeature.TARGET_TEMPERATURE


def test_supported_features_follow_current_operation_mode_capability() -> None:
    """Re-evaluate target support when the active Bosch mode changes."""
    manual_path = "/heatingCircuits/hc1/manualRoomSetpoint"
    operation_mode_path = "/heatingCircuits/hc1/operationMode"
    runtime, coordinator = _runtime(
        "hc1", "manual", endpoint_writability={manual_path: True}
    )
    entity = BoschHeatingCircuitClimate(runtime, _circuit_paths("hc1"))

    assert entity.supported_features & ClimateEntityFeature.TARGET_TEMPERATURE

    coordinator.set_value(operation_mode_path, "auto")

    assert not (entity.supported_features & ClimateEntityFeature.TARGET_TEMPERATURE)

    coordinator.set_value(operation_mode_path, "manual")

    assert entity.supported_features & ClimateEntityFeature.TARGET_TEMPERATURE


def test_read_only_native_metadata_falls_back_without_writing_bosch() -> None:
    """Never PUT to a setpoint endpoint merely because it exists."""
    path = "/heatingCircuits/hc1/manualRoomSetpoint"
    h60 = _FakeH60()
    runtime, coordinator = _runtime(
        "hc1",
        "manual",
        endpoint_writability={path: False},
        h60=h60,
    )
    entity = BoschHeatingCircuitClimate(runtime, _circuit_paths("hc1"))

    asyncio.run(entity.async_set_temperature(**{ATTR_TEMPERATURE: 23.0}))

    assert coordinator.writes == []
    assert h60.writes == [("0203", 23.0)]


def test_generic_target_is_not_used_when_operation_mode_is_unknown() -> None:
    """Avoid an ambiguous generic write when the active mode is unknown."""
    path = "/heatingCircuits/hc1/temperatureRoomSetpoint"
    runtime, coordinator = _runtime("hc1", "unknown", endpoint_writability={path: True})
    entity = BoschHeatingCircuitClimate(runtime, _circuit_paths("hc1"))

    assert not (entity.supported_features & ClimateEntityFeature.TARGET_TEMPERATURE)
    with pytest.raises(HomeAssistantError, match="verified writable room target"):
        asyncio.run(entity.async_set_temperature(**{ATTR_TEMPERATURE: 21.0}))
    assert coordinator.writes == []


def test_missing_native_capability_without_h60_is_read_only() -> None:
    """A circuit without writable metadata must not advertise target control."""
    runtime, coordinator = _runtime("hc2", "auto", endpoint_writability={})
    entity = BoschHeatingCircuitClimate(runtime, _circuit_paths("hc2"))

    assert not (entity.supported_features & ClimateEntityFeature.TARGET_TEMPERATURE)
    with pytest.raises(HomeAssistantError, match="verified writable room target"):
        asyncio.run(entity.async_set_temperature(**{ATTR_TEMPERATURE: 21.0}))
    assert coordinator.writes == []
