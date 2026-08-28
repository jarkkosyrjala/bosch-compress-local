"""Tests for Bosch-native heating-circuit target writes."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast

import pytest
from homeassistant.components.climate.const import (
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE
from homeassistant.exceptions import HomeAssistantError

from custom_components.bosch_compress.climate import (
    BoschHeatingCircuitClimate,
    _circuit_paths,
    async_setup_entry,
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
        self.registered_paths: set[str] = set()

    def endpoint(self, path: str) -> BoschEndpoint | None:
        """Return discovered endpoint metadata."""
        return self.discovered_endpoints.get(path)

    async def async_write(self, path: str, value: Any) -> None:
        """Record a native Bosch write."""
        self.writes.append((path, value))

    def async_add_listener(self, update_callback: Any, context: Any = None) -> Any:
        """Return a no-op coordinator listener unsubscribe callback."""
        del update_callback
        if isinstance(context, str):
            self.registered_paths.add(context)
        return lambda: None

    def register_path(self, path: str) -> Any:
        """Record a path that the real coordinator will retry each refresh."""
        self.registered_paths.add(path)
        return lambda: None

    def set_value(self, path: str, value: Any) -> None:
        """Replace one immutable endpoint snapshot."""
        self.discovered_endpoints[path] = replace(
            self.discovered_endpoints[path], value=value
        )

    def add_circuit(self, circuit_id: str) -> None:
        """Make a previously missing circuit's required endpoints available."""
        base = f"/heatingCircuits/{circuit_id}"
        values: dict[str, Any] = {
            f"{base}/roomtemperature": 21.0,
            f"{base}/currentRoomSetpoint": 21.5,
            f"{base}/operationMode": "auto",
        }
        self.discovered_endpoints.update(
            {
                path: BoschEndpoint(
                    path=path,
                    value=value,
                    value_type=(
                        "floatValue"
                        if isinstance(value, int | float)
                        else "stringValue"
                    ),
                    writable=False,
                )
                for path, value in values.items()
            }
        )


def _runtime(
    circuit_id: str,
    operation_mode: str,
    *,
    endpoint_writability: dict[str, bool],
    operation_mode_writable: bool = True,
    operation_mode_allowed_values: tuple[str, ...] = ("auto", "manual"),
    include_operation_mode: bool = True,
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
    operation_mode_path = f"{base}/operationMode"
    if include_operation_mode:
        coordinator.discovered_endpoints[operation_mode_path] = replace(
            coordinator.discovered_endpoints[operation_mode_path],
            writable=operation_mode_writable,
            allowed_values=operation_mode_allowed_values,
        )
    else:
        coordinator.discovered_endpoints.pop(operation_mode_path)
    runtime = BoschRuntimeData(
        coordinator=cast(Any, coordinator),
        gateway_uuid="test-gateway",
        host="192.0.2.1",
    )
    return runtime, coordinator


def test_boot_missing_circuits_recover_in_place_without_duplicates() -> None:
    """Create stable HC entities before their transiently missing trees recover."""
    coordinator = _FakeCoordinator({})
    runtime = BoschRuntimeData(
        coordinator=cast(Any, coordinator),
        gateway_uuid="test-gateway",
        host="192.0.2.1",
    )
    config_entry = SimpleNamespace(runtime_data=runtime)
    entities: list[BoschHeatingCircuitClimate] = []

    asyncio.run(
        async_setup_entry(cast(Any, None), cast(Any, config_entry), entities.extend)
    )

    assert [entity.unique_id for entity in entities] == [
        "test-gateway_hc1",
        "test-gateway_hc2",
    ]
    assert all(not entity.available for entity in entities)

    async def add_entities_to_hass() -> None:
        for entity in entities:
            await entity.async_added_to_hass()

    asyncio.run(add_entities_to_hass())

    assert {
        f"/heatingCircuits/{circuit_id}/{endpoint_name}"
        for circuit_id in ("hc1", "hc2")
        for endpoint_name in (
            "roomtemperature",
            "currentRoomSetpoint",
            "manualRoomSetpoint",
            "operationMode",
            "temporaryRoomSetpoint",
            "temperatureRoomSetpoint",
        )
    }.issubset(coordinator.registered_paths)

    coordinator.add_circuit("hc2")

    assert entities[0].available is False
    assert entities[1].available is True

    coordinator.add_circuit("hc1")

    assert all(entity.available for entity in entities)
    assert len(entities) == 2


@pytest.mark.parametrize("circuit_id", ["hc1", "hc2"])
@pytest.mark.parametrize(
    ("operation_mode", "endpoint_name"),
    [
        ("manual", "manualRoomSetpoint"),
        ("auto", "temporaryRoomSetpoint"),
    ],
)
def test_mode_specific_native_target_uses_matching_bosch_endpoint(
    circuit_id: str, operation_mode: str, endpoint_name: str
) -> None:
    """Each circuit writes the native target matching its operating mode."""
    path = f"/heatingCircuits/{circuit_id}/{endpoint_name}"
    generic_path = f"/heatingCircuits/{circuit_id}/temperatureRoomSetpoint"
    runtime, coordinator = _runtime(
        circuit_id,
        operation_mode,
        endpoint_writability={path: True, generic_path: True},
    )
    entity = BoschHeatingCircuitClimate(runtime, _circuit_paths(circuit_id))

    asyncio.run(entity.async_set_temperature(**{ATTR_TEMPERATURE: 22.5}))

    assert coordinator.writes == [(path, 22.5)]


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


def test_hvac_modes_use_exact_writable_values_advertised_by_bosch() -> None:
    """Expose known writable modes and preserve the gateway's exact casing."""
    runtime, coordinator = _runtime(
        "hc1",
        "manual",
        endpoint_writability={},
        operation_mode_allowed_values=("AUTO", "Manual"),
    )
    entity = BoschHeatingCircuitClimate(runtime, _circuit_paths("hc1"))

    assert entity.hvac_mode is HVACMode.HEAT
    assert entity.hvac_modes == [HVACMode.AUTO, HVACMode.HEAT]

    asyncio.run(entity.async_set_hvac_mode(HVACMode.AUTO))

    assert coordinator.writes == [("/heatingCircuits/hc1/operationMode", "AUTO")]


_MISSING = object()


@pytest.mark.parametrize(
    ("status", "pump", "source", "expected"),
    [
        (_MISSING, 20, 40, None),
        ("UNKNOWN", 20, 40, None),
        ("ACTIVE", _MISSING, 40, None),
        ("ACTIVE", 20, _MISSING, None),
        ("ACTIVE", "20", 40, None),
        ("ACTIVE", 20, "40", None),
        ("ACTIVE", True, 40, None),
        ("ACTIVE", 20, False, None),
        ("INACTIVE", _MISSING, _MISSING, HVACAction.IDLE),
        ("ACTIVE", 0, 40, HVACAction.IDLE),
        ("ACTIVE", 20, 0, HVACAction.IDLE),
        ("ACTIVE", 20, 40, HVACAction.HEATING),
    ],
)
def test_hvac_action_requires_explicit_status_and_complete_numeric_telemetry(
    status: object,
    pump: object,
    source: object,
    expected: HVACAction | None,
) -> None:
    """Report unknown action when status or active telemetry is incomplete."""
    runtime, coordinator = _runtime("hc1", "manual", endpoint_writability={})
    paths = _circuit_paths("hc1")
    telemetry = {
        paths.status: status,
        paths.pump_modulation: pump,
        "/heatSources/actualModulation": source,
    }
    for path, value in telemetry.items():
        if value is _MISSING:
            continue
        coordinator.discovered_endpoints[path] = BoschEndpoint(
            path=path,
            value=value,
            value_type=(
                "floatValue"
                if isinstance(value, int | float) and not isinstance(value, bool)
                else "stringValue"
            ),
            writable=False,
        )

    entity = BoschHeatingCircuitClimate(runtime, paths)

    assert entity.hvac_action is expected


@pytest.mark.parametrize(
    (
        "include_operation_mode",
        "operation_mode_writable",
        "allowed_values",
        "expected_current_mode",
        "expected_modes",
    ),
    [
        (False, True, ("auto", "manual"), None, []),
        (True, False, ("auto", "manual"), HVACMode.HEAT, []),
        (True, True, ("auto",), HVACMode.HEAT, [HVACMode.AUTO]),
    ],
)
def test_hvac_mode_write_refuses_missing_read_only_or_disallowed_endpoint(
    include_operation_mode: bool,
    operation_mode_writable: bool,
    allowed_values: tuple[str, ...],
    expected_current_mode: HVACMode | None,
    expected_modes: list[HVACMode],
) -> None:
    """Report current mode independently while refusing unsupported writes."""
    runtime, coordinator = _runtime(
        "hc1",
        "manual",
        endpoint_writability={},
        operation_mode_writable=operation_mode_writable,
        operation_mode_allowed_values=allowed_values,
        include_operation_mode=include_operation_mode,
    )
    entity = BoschHeatingCircuitClimate(runtime, _circuit_paths("hc1"))

    assert entity.hvac_mode is expected_current_mode
    assert entity.hvac_modes == expected_modes
    with pytest.raises(HomeAssistantError, match="Unsupported HVAC mode"):
        asyncio.run(entity.async_set_hvac_mode(HVACMode.HEAT))
    assert coordinator.writes == []


def test_read_only_native_metadata_disables_target_write() -> None:
    """Never PUT to a setpoint endpoint merely because it exists."""
    path = "/heatingCircuits/hc1/manualRoomSetpoint"
    runtime, coordinator = _runtime(
        "hc1",
        "manual",
        endpoint_writability={path: False},
    )
    entity = BoschHeatingCircuitClimate(runtime, _circuit_paths("hc1"))

    assert not (entity.supported_features & ClimateEntityFeature.TARGET_TEMPERATURE)
    with pytest.raises(HomeAssistantError, match="verified writable room target"):
        asyncio.run(entity.async_set_temperature(**{ATTR_TEMPERATURE: 23.0}))

    assert coordinator.writes == []


def test_generic_target_is_not_used_when_operation_mode_is_unknown() -> None:
    """Avoid an ambiguous generic write when the active mode is unknown."""
    path = "/heatingCircuits/hc1/temperatureRoomSetpoint"
    runtime, coordinator = _runtime("hc1", "unknown", endpoint_writability={path: True})
    entity = BoschHeatingCircuitClimate(runtime, _circuit_paths("hc1"))

    assert not (entity.supported_features & ClimateEntityFeature.TARGET_TEMPERATURE)
    with pytest.raises(HomeAssistantError, match="verified writable room target"):
        asyncio.run(entity.async_set_temperature(**{ATTR_TEMPERATURE: 21.0}))
    assert coordinator.writes == []


def test_missing_native_capability_is_read_only() -> None:
    """A circuit without writable metadata must not advertise target control."""
    runtime, coordinator = _runtime("hc2", "auto", endpoint_writability={})
    entity = BoschHeatingCircuitClimate(runtime, _circuit_paths("hc2"))

    assert not (entity.supported_features & ClimateEntityFeature.TARGET_TEMPERATURE)
    with pytest.raises(HomeAssistantError, match="verified writable room target"):
        asyncio.run(entity.async_set_temperature(**{ATTR_TEMPERATURE: 21.0}))
    assert coordinator.writes == []
