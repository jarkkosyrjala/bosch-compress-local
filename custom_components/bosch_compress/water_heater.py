"""Domestic hot-water entity for Bosch Compress."""

from __future__ import annotations

from typing import Any, override

from homeassistant.components.water_heater import (
    WaterHeaterEntity,
    WaterHeaterEntityFeature,
)
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import BoschEntity, string_values
from .runtime import BoschConfigEntry, BoschRuntimeData
from .topology import effective_topology

PARALLEL_UPDATES = 1

_DHW_PROGRAM_LABELS = {
    "off": "Off",
    "low": "Eco / Normal",
    "high": "Comfort",
    "ownprogram": "Manual time program",
    "eco": "Eco+",
}


def _dhw_path(circuit_id: str, endpoint_name: str) -> str:
    """Return one endpoint path for a domestic-hot-water circuit."""
    return f"/dhwCircuits/{circuit_id}/{endpoint_name}"


def _dhw_name(circuit_id: str) -> str:
    """Return a stable, readable entity name for a DHW circuit."""
    if circuit_id == "dhw1":
        return "Domestic hot water"
    return f"Domestic hot water {circuit_id.removeprefix('dhw')}"


class BoschDhwWaterHeater(BoschEntity, WaterHeaterEntity):
    """Represent domestic hot water without fabricating target writes."""

    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_min_temp = 30.0
    _attr_max_temp = 70.0
    _attr_target_temperature_step = 0.5

    def __init__(
        self, runtime_data: BoschRuntimeData, circuit_id: str = "dhw1"
    ) -> None:
        """Initialize the DHW entity."""
        self._circuit_id = circuit_id
        self._actual_temperature_path = _dhw_path(circuit_id, "actualTemp")
        self._current_setpoint_path = _dhw_path(circuit_id, "currentSetpoint")
        self._status_path = _dhw_path(circuit_id, "status")
        self._operation_mode_path = _dhw_path(circuit_id, "operationMode")
        super().__init__(
            runtime_data,
            self._actual_temperature_path,
            name=_dhw_name(circuit_id),
            enabled_default=True,
        )
        self._attr_unique_id = f"{runtime_data.gateway_uuid}_{circuit_id}"

    def _operation_to_value(self) -> dict[str, str]:
        """Return the current writable native DHW program mapping."""
        endpoint = self.coordinator.endpoint(self._operation_mode_path)
        if (
            endpoint is None
            or not endpoint.writable
            or endpoint.value_type != "stringValue"
        ):
            return {}
        return {
            _DHW_PROGRAM_LABELS.get(value.casefold(), value): value
            for value in string_values(endpoint.allowed_values)
        }

    def _value(self, path: str) -> Any:
        endpoint = self.coordinator.endpoint(path)
        return endpoint.value if endpoint is not None else None

    @property
    @override
    def available(self) -> bool:
        """Return availability when the circuit's primary temperature exists."""
        return super().available

    @property
    @override
    def current_temperature(self) -> float | None:
        """Return the measured domestic hot-water temperature."""
        value = self._value(self._actual_temperature_path)
        return float(value) if isinstance(value, int | float) else None

    @property
    @override
    def target_temperature(self) -> float | None:
        """Return the controller's active, read-only DHW setpoint."""
        value = self._value(self._current_setpoint_path)
        return float(value) if isinstance(value, int | float) else None

    @property
    @override
    def supported_features(self) -> WaterHeaterEntityFeature:
        """Offer operation-mode control only while the endpoint is writable."""
        if self._operation_to_value():
            return WaterHeaterEntityFeature.OPERATION_MODE
        return WaterHeaterEntityFeature(0)

    @property
    @override
    def operation_list(self) -> list[str] | None:
        """Return current native DHW programs in canonical HA form."""
        operation_to_value = self._operation_to_value()
        return list(operation_to_value) or None

    @property
    @override
    def current_operation(self) -> str | None:
        """Return the native program when operation-mode control is usable."""
        operation_to_value = self._operation_to_value()
        value = self._value(self._operation_mode_path)
        if not operation_to_value or not isinstance(value, str):
            return None
        value_to_operation = {
            native.casefold(): operation
            for operation, native in operation_to_value.items()
        }
        return value_to_operation.get(value.casefold())

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose physical activity separately from the selected program."""
        return {
            "bosch_status": self._value(self._status_path),
            "target_is_read_only": True,
        }

    @override
    async def async_added_to_hass(self) -> None:
        """Register all compound Bosch inputs."""
        await super().async_added_to_hass()
        for path in (
            self._actual_temperature_path,
            self._current_setpoint_path,
            self._status_path,
            self._operation_mode_path,
        ):
            self.async_on_remove(self.coordinator.register_path(path))

    @override
    async def async_set_operation_mode(self, operation_mode: str) -> None:
        """Write the Bosch-native DHW operation mode."""
        operation_to_value = self._operation_to_value()
        if operation_mode not in operation_to_value:
            raise HomeAssistantError(f"Unsupported DHW program: {operation_mode}")
        await self.coordinator.async_write(
            self._operation_mode_path, operation_to_value[operation_mode]
        )


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: BoschConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up every discovered or previously observed DHW circuit."""
    del hass
    runtime_data = config_entry.runtime_data
    async_add_entities(
        BoschDhwWaterHeater(runtime_data, circuit_id)
        for circuit_id in sorted(effective_topology(runtime_data).dhw_circuits)
    )
