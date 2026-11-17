"""Exercise native Bosch temperatures through Home Assistant state and services."""

from __future__ import annotations

import asyncio
import importlib
import logging
from dataclasses import replace
from pathlib import Path

import pytest
from homeassistant import loader
from homeassistant.components import climate, number, sensor, water_heater
from homeassistant.components.sensor import SensorStateClass
from homeassistant.config_entries import ConfigEntries
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import area_registry, device_registry, entity_registry, frame
from homeassistant.setup import async_setup_component
from homeassistant.util.unit_system import METRIC_SYSTEM, US_CUSTOMARY_SYSTEM

from custom_components.bosch_compress.models import BoschEndpoint
from custom_components.bosch_compress.number import BoschEndpointNumber
from custom_components.bosch_compress.sensor import BoschEndpointSensor
from tests.test_native_climate import _runtime as heating_runtime
from tests.test_native_dhw import _endpoint
from tests.test_native_dhw import _runtime as dhw_runtime


@pytest.mark.parametrize(
    ("fahrenheit", "current", "target", "dhw_current", "dhw_target", "service_target"),
    [(False, 21.0, 21.5, 48.0, 54.0, 22.5), (True, 70.0, 71.0, 118.0, 129.0, 72.5)],
)
def test_native_temperatures_through_ha(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    fahrenheit: bool,
    current: float,
    target: float,
    dhw_current: float,
    dhw_target: float,
    service_target: float,
) -> None:
    """Publish converted states, recover in place and write Celsius exactly once."""
    caplog.set_level(logging.WARNING)

    async def run() -> None:
        # Reimport under log capture: legacy property overrides warn at class creation.
        climate_module = importlib.reload(
            importlib.import_module("custom_components.bosch_compress.climate")
        )
        water_module = importlib.reload(
            importlib.import_module("custom_components.bosch_compress.water_heater")
        )
        hass = HomeAssistant(str(tmp_path))
        hass.config_entries = ConfigEntries(hass, {})
        loader.async_setup(hass)
        frame.async_setup(hass)
        device_registry.async_setup(hass)
        await area_registry.async_load(hass)
        await device_registry.async_load(hass)
        await hass.config_entries.async_initialize()
        await entity_registry.async_load(hass)
        hass.config.units = US_CUSTOMARY_SYSTEM if fahrenheit else METRIC_SYSTEM
        assert await async_setup_component(hass, "climate", {})
        assert await async_setup_component(hass, "water_heater", {})
        assert await async_setup_component(hass, "sensor", {})
        assert await async_setup_component(hass, "number", {})
        climates = []
        coordinators = []
        for circuit, mode, setter in (
            ("hc1", "manual", "manualRoomSetpoint"),
            ("hc2", "auto", "temporaryRoomSetpoint"),
        ):
            runtime, coordinator = heating_runtime(
                circuit,
                mode,
                endpoint_writability={f"/heatingCircuits/{circuit}/{setter}": True},
            )
            entity = climate_module.BoschHeatingCircuitClimate(
                runtime, climate_module._circuit_paths(circuit)
            )
            entity.entity_id = f"climate.bosch_{circuit}"
            climates.append(entity)
            coordinators.append(coordinator)
        dhw = dhw_runtime(
            [
                _endpoint("/dhwCircuits/dhw1/actualTemp", 48.0),
                _endpoint("/dhwCircuits/dhw1/currentSetpoint", 54.0),
            ]
        )
        tank = water_module.BoschDhwWaterHeater(dhw)
        tank.entity_id = "water_heater.bosch_dhw"
        # An invalid very first reading must still create a numeric sensor with
        # measurement statistics, and a setting with its advertised write bounds.
        outdoor_path = "/system/sensors/temperatures/outdoor_t1"
        setting_path = "/dhwCircuits/dhw1/singleChargeSetpoint"
        for path, writable in ((outdoor_path, False), (setting_path, True)):
            coordinators[0].discovered_endpoints[path] = BoschEndpoint(
                path,
                -3276.8,
                "floatValue",
                writable,
                unit="C",
                minimum=5,
                maximum=80,
            )
        outdoor = BoschEndpointSensor(climates[0]._runtime_data, outdoor_path)
        outdoor.entity_id = "sensor.bosch_outdoor"
        setting = BoschEndpointNumber(climates[0]._runtime_data, setting_path)
        setting.entity_id = "number.bosch_setting"
        try:
            await hass.data[climate.DATA_COMPONENT].async_add_entities(climates)
            await hass.data[water_heater.DATA_COMPONENT].async_add_entities([tank])
            await hass.data[sensor.DATA_COMPONENT].async_add_entities([outdoor])
            await hass.data[number.DATA_COMPONENT].async_add_entities([setting])
            await hass.async_block_till_done()
            assert outdoor.state_class is SensorStateClass.MEASUREMENT
            for entity in (outdoor, setting):
                assert entity.available
                assert hass.states.get(entity.entity_id).state == "unknown"
                assert "raw_value" not in hass.states.get(entity.entity_id).attributes
            assert (setting.native_min_value, setting.native_max_value) == (5, 80)
            for path, entity, valid in (
                (outdoor_path, outdoor, -20.0),
                (setting_path, setting, 50.0),
            ):
                endpoints = coordinators[0].discovered_endpoints
                endpoints[path] = replace(endpoints[path], value=valid)
                entity.async_write_ha_state()
                assert entity.native_value == valid
                state = hass.states.get(entity.entity_id)
                assert state.state not in ("unknown", "unavailable")
                assert float(state.state) == (
                    valid * 1.8 + 32 if fahrenheit and entity is outdoor else valid
                )
                endpoints[path] = replace(endpoints[path], value=float("inf"))
                entity.async_write_ha_state()
                assert hass.states.get(entity.entity_id).state == "unknown"
                endpoints[path] = replace(endpoints[path], value=valid)
                entity.async_write_ha_state()
            for entity in climates:
                assert entity.native_temperature_unit == "°C"
                assert entity.native_current_temperature == 21.0
                assert entity.native_target_temperature == 21.5
                assert (entity.min_temp, entity.max_temp) == (13.0, 29.0)
                assert entity.target_temperature_step == 0.5
                state = hass.states.get(entity.entity_id)
                assert state is not None
                assert state.attributes["temperature_unit"] == (
                    "°F" if fahrenheit else "°C"
                )
                assert state.attributes["current_temperature"] == current
                assert state.attributes["temperature"] == target
                await hass.services.async_call(
                    "climate",
                    "set_temperature",
                    {"entity_id": entity.entity_id, "temperature": service_target},
                    blocking=True,
                )
            assert coordinators[0].writes == [
                ("/heatingCircuits/hc1/manualRoomSetpoint", 22.5)
            ]
            assert coordinators[1].writes == [
                ("/heatingCircuits/hc2/temporaryRoomSetpoint", 22.5)
            ]
            assert tank.native_current_temperature == 48.0
            assert tank.native_target_temperature == 54.0
            assert (tank.min_temp, tank.max_temp, tank.target_temperature_step) == (
                30.0,
                70.0,
                0.5,
            )
            state = hass.states.get(tank.entity_id)
            assert state is not None
            assert state.attributes["temperature_unit"] == (
                "°F" if fahrenheit else "°C"
            )
            assert state.attributes["current_temperature"] == dhw_current
            assert state.attributes["temperature"] == dhw_target
            assert state.attributes["target_is_read_only"] is True
            with pytest.raises(HomeAssistantError):
                await hass.services.async_call(
                    "water_heater",
                    "set_temperature",
                    {"entity_id": tank.entity_id, "temperature": dhw_target},
                    blocking=True,
                )
            assert dhw.coordinator.writes == []

            # Missing readings are unknown; missing primary paths are unavailable.
            for entity, coordinator, measured, setpoint in (
                (
                    climates[0],
                    coordinators[0],
                    "/heatingCircuits/hc1/roomtemperature",
                    "/heatingCircuits/hc1/currentRoomSetpoint",
                ),
                (
                    tank,
                    dhw.coordinator,
                    "/dhwCircuits/dhw1/actualTemp",
                    "/dhwCircuits/dhw1/currentSetpoint",
                ),
            ):
                endpoints = coordinator.discovered_endpoints
                saved = dict(endpoints)
                features = entity.supported_features
                for invalid in (
                    None,
                    -3276.8,
                    -3200.0,
                    3200.0,
                    float("inf"),
                    True,
                    "bad",
                ):
                    for path in (measured, setpoint):
                        endpoints[path] = replace(
                            saved[path],
                            value=invalid,
                            unit="C",
                            value_type="floatValue",
                        )
                    entity.async_write_ha_state()
                    assert entity.available and entity.supported_features == features
                    state = hass.states.get(entity.entity_id)
                    assert state.attributes["current_temperature"] is None
                    assert state.attributes["temperature"] is None
                    assert "raw_value" not in state.attributes
                    assert climates[1].native_current_temperature == 21.0
                    # Every invalid reading recovers without entity recreation.
                    endpoints.update(saved)
                    entity.async_write_ha_state()
                    assert (
                        hass.states.get(entity.entity_id).attributes[
                            "current_temperature"
                        ]
                        is not None
                    )
                endpoints.pop(measured)
                endpoints.pop(setpoint)
                assert entity.native_current_temperature is None
                assert entity.native_target_temperature is None
                entity.async_write_ha_state()
                assert hass.states.get(entity.entity_id).state == "unavailable"
                endpoints.update(saved)
                entity.async_write_ha_state()
                assert hass.states.get(entity.entity_id).state != "unavailable"
            assert len(list(hass.data[climate.DATA_COMPONENT].entities)) == 2
            assert len(list(hass.data[water_heater.DATA_COMPONENT].entities)) == 1
        finally:
            await hass.async_stop()

    asyncio.run(run())
    assert not [record for record in caplog.records if record.levelno >= logging.ERROR]
    assert not [
        record.message
        for record in caplog.records
        if "temperature" in record.message.lower()
        and any(word in record.message.lower() for word in ("deprecated", "legacy"))
    ]
