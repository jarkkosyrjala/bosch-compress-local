"""Sensor entities for Bosch Compress."""

from __future__ import annotations

import json
from typing import Any, override

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
    UnitOfTime,
    UnitOfVolumeFlowRate,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import (
    DEFAULT_ENABLED_ENDPOINTS,
    BoschEntity,
    H60Entity,
    is_binary_endpoint,
)
from .h60 import H60_REGISTERS, H60RegisterDescription, H60RegisterKind
from .runtime import BoschConfigEntry, BoschRuntimeData

PARALLEL_UPDATES = 0

_DIAGNOSTIC_ENDPOINTS = frozenset({"/notifications", "/system/healthStatus"})
_MAX_COMPLEX_ATTRIBUTE_BYTES = 4096


def _bounded_complex_value(value: list[Any] | tuple[Any, ...] | dict[str, Any]) -> Any:
    """Return complex endpoint data only while it remains safe for HA state."""
    try:
        encoded = json.dumps(value, default=str, separators=(",", ":"))
    except TypeError, ValueError:
        return {"omitted": True, "reason": "not JSON serializable"}
    if len(encoded.encode()) <= _MAX_COMPLEX_ATTRIBUTE_BYTES:
        return value
    return {
        "omitted": True,
        "reason": "value exceeds Home Assistant state attribute limit",
        "item_count": len(value),
    }


def _is_control_endpoint(endpoint: Any) -> bool:
    """Return whether another platform provides the writable endpoint."""
    if not endpoint.writable:
        return False
    if is_binary_endpoint(endpoint):
        return True
    if endpoint.value_type == "floatValue":
        return endpoint.minimum is not None and endpoint.maximum is not None
    if endpoint.value_type == "stringValue":
        return True
    return False


class BoschEndpointSensor(BoschEntity, SensorEntity):
    """Represent one scalar or complex Bosch endpoint as a sensor."""

    def __init__(self, runtime_data: BoschRuntimeData, path: str) -> None:
        """Initialize an endpoint sensor."""
        super().__init__(
            runtime_data,
            path,
            enabled_default=path in DEFAULT_ENABLED_ENDPOINTS,
            diagnostic=(
                path not in DEFAULT_ENABLED_ENDPOINTS or path in _DIAGNOSTIC_ENDPOINTS
            ),
        )
        endpoint = runtime_data.coordinator.discovered_endpoints[path]
        self._configure_measurement(endpoint)

    def _configure_measurement(self, endpoint: Any) -> None:
        """Configure Home Assistant units and device classes."""
        unit = endpoint.unit
        if unit in {"C", "°C"}:
            self._attr_device_class = SensorDeviceClass.TEMPERATURE
            self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
        elif unit == "%":
            self._attr_native_unit_of_measurement = PERCENTAGE
        elif unit == "kW":
            self._attr_device_class = SensorDeviceClass.POWER
            self._attr_native_unit_of_measurement = UnitOfPower.KILO_WATT
        elif unit == "kWh":
            self._attr_device_class = SensorDeviceClass.ENERGY
            self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        elif unit in {"s", "sec"}:
            self._attr_device_class = SensorDeviceClass.DURATION
            self._attr_native_unit_of_measurement = UnitOfTime.SECONDS
        elif unit in {"min", "mins"}:
            self._attr_device_class = SensorDeviceClass.DURATION
            self._attr_native_unit_of_measurement = UnitOfTime.MINUTES
        elif unit == "l/min":
            self._attr_device_class = SensorDeviceClass.VOLUME_FLOW_RATE
            self._attr_native_unit_of_measurement = (
                UnitOfVolumeFlowRate.LITERS_PER_MINUTE
            )
        elif unit and unit.strip():
            self._attr_native_unit_of_measurement = unit.strip()

        if (
            isinstance(endpoint.value, int | float)
            and not isinstance(endpoint.value, bool)
            and self.device_class is not SensorDeviceClass.ENERGY
        ):
            self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    @override
    def native_value(self) -> str | int | float | None:
        """Return the current endpoint value."""
        value = self.endpoint.value
        if value is None or isinstance(value, str | int | float):
            return value
        if isinstance(value, list | tuple | dict):
            return len(value)
        return str(value)

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose metadata and bounded complex data for diagnostics."""
        endpoint = self.endpoint
        attributes: dict[str, Any] = {
            "endpoint": self._path,
            "value_type": endpoint.value_type,
            "writeable": endpoint.writable,
        }
        if isinstance(endpoint.value, list | tuple | dict):
            attributes["value"] = _bounded_complex_value(endpoint.value)
        if endpoint.allowed_values:
            attributes["allowed_values"] = list(endpoint.allowed_values)
        if self._path == "/notifications":
            attributes["semantics"] = "stored notification history, not active alarm"
        elif self._path == "/system/healthStatus":
            attributes["semantics"] = "gateway health, not current alarm state"
        return attributes


class H60SupplementalSensor(H60Entity, SensorEntity):
    """Represent diagnostic data not exposed by the Bosch IP module."""

    def __init__(
        self,
        runtime_data: BoschRuntimeData,
        description: H60RegisterDescription,
    ) -> None:
        """Initialize an H60 supplemental sensor."""
        super().__init__(runtime_data, description)
        self._attr_native_unit_of_measurement = description.unit
        if description.unit == UnitOfTemperature.CELSIUS:
            self._attr_device_class = SensorDeviceClass.TEMPERATURE
            self._attr_state_class = SensorStateClass.MEASUREMENT
        elif description.unit == UnitOfPower.KILO_WATT:
            self._attr_device_class = SensorDeviceClass.POWER
            self._attr_state_class = SensorStateClass.MEASUREMENT
        elif description.unit == PERCENTAGE:
            self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    @override
    def native_value(self) -> int | float | None:
        """Return the latest numeric H60 value."""
        return self.h60_value

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any]:
        """Identify the supplemental source and register."""
        return {
            "source": "Husdata H60 MQTT",
            "register": self._description.register,
            "description": self._description.description,
        }


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: BoschConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up discovered Bosch endpoint sensors."""
    del hass
    runtime_data = config_entry.runtime_data
    entities = [
        BoschEndpointSensor(runtime_data, path)
        for path, endpoint in runtime_data.coordinator.discovered_endpoints.items()
        if not endpoint.is_reference
        and not is_binary_endpoint(endpoint)
        and not _is_control_endpoint(endpoint)
    ]
    if runtime_data.h60 is not None:
        entities.extend(
            H60SupplementalSensor(runtime_data, description)
            for description in H60_REGISTERS.values()
            if description.kind is H60RegisterKind.SENSOR
            and description.register != "3108"
        )
    async_add_entities(entities)
