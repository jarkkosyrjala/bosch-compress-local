"""Focused tests for the Bosch Compress Home Assistant entity layer."""

from __future__ import annotations

import asyncio
import importlib
from importlib.metadata import version
from typing import Any, cast

import pytest
from homeassistant.components.climate import HVACAction, HVACMode
from homeassistant.components.water_heater import WaterHeaterEntityFeature
from homeassistant.const import ATTR_TEMPERATURE

from custom_components.bosch_compress.climate import (
    BoschHeatingCircuitClimate,
    _circuit_paths,
)
from custom_components.bosch_compress.const import PLATFORMS
from custom_components.bosch_compress.h60 import H60_REGISTERS
from custom_components.bosch_compress.models import BoschEndpoint
from custom_components.bosch_compress.runtime import BoschRuntimeData
from custom_components.bosch_compress.select import H60SupplementalSelect
from custom_components.bosch_compress.sensor import BoschEndpointSensor
from custom_components.bosch_compress.switch import H60SupplementalSwitch
from custom_components.bosch_compress.water_heater import BoschDhwWaterHeater


class _FakeCoordinator:
    """Minimal coordinator surface used by the entities under test."""

    def __init__(self, values: dict[str, Any]) -> None:
        self.last_update_success = True
        self.discovered_endpoints = {
            path: BoschEndpoint(
                path=path,
                value=value,
                value_type=(
                    "floatValue" if isinstance(value, int | float) else "stringValue"
                ),
                writable=False,
            )
            for path, value in values.items()
        }
        self.writes: list[tuple[str, Any]] = []

    def endpoint(self, path: str) -> BoschEndpoint | None:
        """Return a discovered endpoint."""
        return self.discovered_endpoints.get(path)

    async def async_write(self, path: str, value: Any) -> None:
        """Record a coordinator write without using the network."""
        self.writes.append((path, value))

    def async_add_listener(self, update_callback: Any, context: Any = None) -> Any:
        """Return a no-op coordinator unsubscribe callback."""
        del update_callback, context
        return lambda: None

    def register_path(self, path: str) -> Any:
        """Return a no-op endpoint-registration unsubscribe callback."""
        del path
        return lambda: None


class _FakeH60:
    """In-memory substitute for the optional H60 MQTT transport."""

    def __init__(self, values: dict[str, int | float] | None = None) -> None:
        self.values = dict(values or {})
        self.writes: list[tuple[str, int | float]] = []

    def value(self, register: str) -> int | float | None:
        """Return the latest register value."""
        return self.values.get(register)

    async def async_set_value(self, register: str, value: int | float) -> None:
        """Record the exact outgoing numeric register payload."""
        self.writes.append((register, value))

    def add_listener(self, listener: Any) -> Any:
        """Return a no-op H60 unsubscribe callback."""
        del listener
        return lambda: None


def _runtime(values: dict[str, Any], h60: _FakeH60 | None = None) -> BoschRuntimeData:
    """Build entity runtime data backed only by lightweight fakes."""
    return BoschRuntimeData(
        coordinator=cast(Any, _FakeCoordinator(values)),
        gateway_uuid="test-gateway",
        host="192.0.2.1",
        h60=cast(Any, h60),
    )


def _heating_values() -> dict[str, Any]:
    """Return two independent circuit snapshots and one heat-source snapshot."""
    return {
        "/heatingCircuits/hc1/roomtemperature": 21.25,
        "/heatingCircuits/hc1/currentRoomSetpoint": 22.5,
        "/heatingCircuits/hc1/actualSupplyTemperature": 31.0,
        "/heatingCircuits/hc1/supplyTemperatureSetpoint": 32.0,
        "/heatingCircuits/hc1/operationMode": "manual",
        "/heatingCircuits/hc1/pumpModulation": 35,
        "/heatingCircuits/hc1/status": "ACTIVE",
        "/heatingCircuits/hc2/roomtemperature": 19.5,
        "/heatingCircuits/hc2/currentRoomSetpoint": 20.0,
        "/heatingCircuits/hc2/actualSupplyTemperature": 26.0,
        "/heatingCircuits/hc2/supplyTemperatureSetpoint": 27.0,
        "/heatingCircuits/hc2/operationMode": "auto",
        "/heatingCircuits/hc2/pumpModulation": 0,
        "/heatingCircuits/hc2/status": "INACTIVE",
        "/heatSources/actualModulation": 42,
    }


def test_every_declared_platform_imports_on_baseline_home_assistant() -> None:
    """Every forwarded platform imports against the pinned HA 2026.8.3 API."""
    assert version("homeassistant") == "2026.8.3"

    for platform in PLATFORMS:
        module = importlib.import_module(
            f"custom_components.bosch_compress.{platform.value}"
        )
        assert callable(module.async_setup_entry)


def test_two_heating_circuits_keep_independent_climate_state() -> None:
    """Both circuits expose their own temperature, mode, and activity."""
    runtime = _runtime(_heating_values(), _FakeH60())
    hc1 = BoschHeatingCircuitClimate(runtime, _circuit_paths("hc1"))
    hc2 = BoschHeatingCircuitClimate(runtime, _circuit_paths("hc2"))

    assert hc1.unique_id == "test-gateway_hc1"
    assert hc2.unique_id == "test-gateway_hc2"
    assert hc1.current_temperature == 21.25
    assert hc1.target_temperature == 22.5
    assert hc1.hvac_mode is HVACMode.HEAT
    assert hc1.hvac_action is HVACAction.HEATING
    assert hc2.current_temperature == 19.5
    assert hc2.target_temperature == 20.0
    assert hc2.hvac_mode is HVACMode.AUTO
    assert hc2.hvac_action is HVACAction.IDLE


@pytest.mark.parametrize(
    "path",
    [
        "/system/appliance/actualSupplyTemperature",
        "/heatingCircuits/hc1/supplyTemperatureSetpoint",
        "/heatingCircuits/hc2/supplyTemperatureSetpoint",
    ],
)
def test_dashboard_supply_temperature_sensors_are_enabled_by_default(
    path: str,
) -> None:
    """Keep every Bosch supply-temperature sensor consumed by the dashboard."""
    entity = BoschEndpointSensor(_runtime({path: 30.0}), path)

    assert entity.entity_registry_enabled_default is True


@pytest.mark.parametrize(
    ("circuit_id", "operation_mode", "expected_register"),
    [
        ("hc1", "manual", "0203"),
        ("hc1", "auto", "020C"),
        ("hc2", "auto", "0220"),
    ],
)
def test_climate_target_writes_route_to_the_correct_h60_register(
    circuit_id: str, operation_mode: str, expected_register: str
) -> None:
    """Manual, automatic, and second-circuit targets never share a register."""
    values = _heating_values()
    values[f"/heatingCircuits/{circuit_id}/operationMode"] = operation_mode
    h60 = _FakeH60()
    entity = BoschHeatingCircuitClimate(
        _runtime(values, h60), _circuit_paths(circuit_id)
    )

    asyncio.run(entity.async_set_temperature(**{ATTR_TEMPERATURE: 21.5}))

    assert h60.writes == [(expected_register, 21.5)]


@pytest.mark.parametrize(
    ("raw_value", "label"),
    [
        (0, "Off"),
        (1, "Eco / Normal"),
        (2, "Comfort"),
        (4, "Manual time program"),
        (5, "Eco+"),
    ],
)
def test_dhw_reports_read_only_target_and_maps_program_values(
    raw_value: int, label: str
) -> None:
    """DHW separates its read-only setpoint from its numeric H60 program."""
    h60 = _FakeH60({"2213": raw_value})
    entity = BoschDhwWaterHeater(
        _runtime(
            {
                "/dhwCircuits/dhw1/actualTemp": 48.25,
                "/dhwCircuits/dhw1/currentSetpoint": 54.0,
                "/dhwCircuits/dhw1/status": "ACTIVE",
            },
            h60,
        )
    )

    assert entity.current_temperature == 48.25
    assert entity.target_temperature == 54.0
    assert entity.current_operation == label
    assert entity.extra_state_attributes["target_is_read_only"] is True
    assert not (entity.supported_features & WaterHeaterEntityFeature.TARGET_TEMPERATURE)

    asyncio.run(entity.async_set_operation_mode(label))
    assert h60.writes == [("2213", raw_value)]


@pytest.mark.parametrize(("raw_value", "label"), H60_REGISTERS["2213"].options)
def test_h60_dhw_select_publishes_exact_program_value(
    raw_value: int, label: str
) -> None:
    """The supplemental select preserves each confirmed numeric program value."""
    h60 = _FakeH60({"2213": raw_value})
    entity = H60SupplementalSelect(_runtime({}, h60), H60_REGISTERS["2213"])

    assert entity.current_option == label
    asyncio.run(entity.async_select_option(label))
    assert h60.writes == [("2213", raw_value)]


def test_h60_extra_warm_water_is_a_binary_switch() -> None:
    """Extra warm water publishes only the confirmed exact 0/1 payloads."""
    h60 = _FakeH60({"1231": 0})
    entity = H60SupplementalSwitch(_runtime({}, h60), H60_REGISTERS["1231"])

    assert entity.is_on is False
    asyncio.run(entity.async_turn_on())
    asyncio.run(entity.async_turn_off())
    assert h60.writes == [("1231", 1), ("1231", 0)]
