"""Focused tests for the Bosch Compress Home Assistant entity layer."""

from __future__ import annotations

import importlib
from importlib.metadata import version
from typing import Any, cast

import pytest
from homeassistant.components.climate import HVACAction, HVACMode
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import UnitOfEnergy
from homeassistant.util import slugify

from custom_components.bosch_compress.climate import (
    BoschHeatingCircuitClimate,
    _circuit_paths,
)
from custom_components.bosch_compress.const import PLATFORMS
from custom_components.bosch_compress.entity import endpoint_name
from custom_components.bosch_compress.models import BoschEndpoint
from custom_components.bosch_compress.runtime import BoschRuntimeData
from custom_components.bosch_compress.sensor import BoschEndpointSensor


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


def _runtime(values: dict[str, Any]) -> BoschRuntimeData:
    """Build entity runtime data backed only by lightweight fakes."""
    return BoschRuntimeData(
        coordinator=cast(Any, _FakeCoordinator(values)),
        gateway_uuid="test-gateway",
        host="192.0.2.1",
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
    runtime = _runtime(_heating_values())
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
        "/heatingCircuits/hc7/supplyTemperatureSetpoint",
        "/dhwCircuits/dhw3/actualTemp",
    ],
)
def test_dashboard_supply_temperature_sensors_are_enabled_by_default(
    path: str,
) -> None:
    """Keep every Bosch supply-temperature sensor consumed by the dashboard."""
    entity = BoschEndpointSensor(_runtime({path: 30.0}), path)

    assert entity.entity_registry_enabled_default is True


def test_endpoint_names_use_sentence_case_and_circuit_abbreviations() -> None:
    """Generic endpoint entities follow current Home Assistant naming style."""
    assert (
        endpoint_name("/heatingCircuits/hc7/actualSupplyTemperature")
        == "Heating circuits HC7 actual supply temperature"
    )
    assert endpoint_name("/dhwCircuits/dhw3/currentSetpoint") == (
        "DHW circuits DHW3 current setpoint"
    )


@pytest.mark.parametrize(
    ("path", "name", "object_id"),
    [
        (
            "/recordings/heatSources/total/energyMonitoring/compressor",
            "Compressor energy today",
            "bosch_compress_compressor_energy_today",
        ),
        (
            "/recordings/heatSources/total/energyMonitoring/eheater",
            "Auxiliary heater energy today",
            "bosch_compress_auxiliary_heater_energy_today",
        ),
    ],
)
def test_daily_energy_sensors_have_energy_dashboard_metadata(
    path: str, name: str, object_id: str
) -> None:
    """Native current-day totals are enabled Energy-compatible counters."""
    runtime = _runtime({})
    runtime.coordinator.discovered_endpoints[path] = BoschEndpoint(
        path=path,
        value=3.5,
        value_type="floatValue",
        writable=False,
        unit="kWh",
    )

    entity = BoschEndpointSensor(runtime, path)

    assert entity.name == name
    assert slugify(f"Bosch Compress {entity.suggested_object_id}") == object_id
    assert entity.native_value == 3.5
    assert entity.device_class is SensorDeviceClass.ENERGY
    assert entity.native_unit_of_measurement == UnitOfEnergy.KILO_WATT_HOUR
    assert entity.state_class is SensorStateClass.TOTAL_INCREASING
    assert entity.entity_registry_enabled_default is True
    assert entity.entity_category is None
