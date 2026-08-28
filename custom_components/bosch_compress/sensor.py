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

from .coordinator import ENERGY_RECORDING_PATHS
from .entity import BoschEntity, is_binary_endpoint, is_default_enabled_endpoint
from .runtime import BoschConfigEntry, BoschRuntimeData

PARALLEL_UPDATES = 0

_DIAGNOSTIC_ENDPOINTS = frozenset({"/notifications", "/system/healthStatus"})
_MAX_COMPLEX_ATTRIBUTE_BYTES = 4096
_ENERGY_RECORDING_NAMES = {
    "/recordings/heatSources/total/energyMonitoring/compressor": (
        "Compressor energy today"
    ),
    "/recordings/heatSources/total/energyMonitoring/eheater": (
        "Auxiliary heater energy today"
    ),
}


def _bounded_complex_value(value: list[Any] | tuple[Any, ...] | dict[str, Any]) -> Any:
    """Return complex endpoint data only while it remains safe for HA state."""
    try:
        encoded = json.dumps(value, default=str, separators=(",", ":"))
    except TypeError:
        return {"omitted": True, "reason": "not JSON serializable"}
    except ValueError:
        return {"omitted": True, "reason": "not JSON serializable"}
    if len(encoded.encode()) <= _MAX_COMPLEX_ATTRIBUTE_BYTES:
        return value
    return {
        "omitted": True,
        "reason": "value exceeds Home Assistant state attribute limit",
        "item_count": len(value),
    }


def _latest_notification(value: Any) -> dict[str, Any] | None:
    """Return the newest stored notification record without implying it is active."""
    if not isinstance(value, list | tuple):
        return None
    records = [record for record in value if isinstance(record, dict)]
    if not records:
        return None
    return max(
        enumerate(records),
        key=lambda indexed: (str(indexed[1].get("t", "")), indexed[0]),
    )[1]


def _notification_identifier(record: dict[str, Any]) -> str:
    """Build a compact identity for one stored notification record."""
    display_code = str(record.get("dcd", "")).strip()
    controller_code = str(record.get("ccd", "")).strip()
    if display_code and controller_code:
        return f"{display_code}-{controller_code}"
    if display_code or controller_code:
        return display_code or controller_code
    timestamp = str(record.get("t", "")).strip()
    return timestamp or "stored notification"


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
        recording_name = _ENERGY_RECORDING_NAMES.get(path)
        super().__init__(
            runtime_data,
            path,
            name=(
                recording_name
                if recording_name is not None
                else "Latest stored notification"
                if path == "/notifications"
                else None
            ),
            enabled_default=(
                is_default_enabled_endpoint(path) or path in ENERGY_RECORDING_PATHS
            ),
            diagnostic=(
                (
                    not is_default_enabled_endpoint(path)
                    and path not in ENERGY_RECORDING_PATHS
                )
                or path in _DIAGNOSTIC_ENDPOINTS
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
            if endpoint.path in ENERGY_RECORDING_PATHS:
                self._attr_state_class = SensorStateClass.TOTAL_INCREASING
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
        if self._path == "/notifications":
            latest = _latest_notification(value)
            if latest is not None:
                return _notification_identifier(latest)
            return "none" if isinstance(value, list | tuple) else None
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
            latest = _latest_notification(endpoint.value)
            attributes["semantics"] = (
                "latest stored notification record, not current alarm state"
            )
            if isinstance(endpoint.value, list | tuple):
                attributes["record_count"] = len(endpoint.value)
            if latest is not None:
                attributes["latest_code"] = _notification_identifier(latest)
                if timestamp := latest.get("t"):
                    attributes["latest_timestamp"] = timestamp
        elif self._path == "/system/healthStatus":
            attributes["semantics"] = "gateway health, not current alarm state"
        return attributes


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
    async_add_entities(entities)
