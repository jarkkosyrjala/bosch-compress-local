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

PARALLEL_UPDATES = 1

_ACTUAL_TEMPERATURE = "/dhwCircuits/dhw1/actualTemp"
_CURRENT_SETPOINT = "/dhwCircuits/dhw1/currentSetpoint"
_STATUS = "/dhwCircuits/dhw1/status"
_OPERATION_MODE = "/dhwCircuits/dhw1/operationMode"
_DHW_PROGRAM_LABELS = {
    "off": "Off",
    "low": "Eco / Normal",
    "high": "Comfort",
    "ownprogram": "Manual time program",
    "eco": "Eco+",
}


class BoschDhwWaterHeater(BoschEntity, WaterHeaterEntity):
    """Represent domestic hot water without fabricating target writes."""

    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_min_temp = 30.0
    _attr_max_temp = 70.0
    _attr_target_temperature_step = 0.5

    def __init__(self, runtime_data: BoschRuntimeData) -> None:
        """Initialize the DHW entity."""
        super().__init__(
            runtime_data,
            _ACTUAL_TEMPERATURE,
            name="Domestic Hot Water",
            enabled_default=True,
        )
        self._attr_unique_id = f"{runtime_data.gateway_uuid}_dhw1"
        self._attr_suggested_object_id = "bosch_compress_dhw"

    def _operation_to_value(self) -> dict[str, str]:
        """Return the current writable native DHW program mapping."""
        endpoint = self.coordinator.endpoint(_OPERATION_MODE)
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
        """Return availability when both essential Bosch readings exist."""
        return (
            super().available
            and self.coordinator.endpoint(_CURRENT_SETPOINT) is not None
            and self.coordinator.endpoint(_STATUS) is not None
        )

    @property
    @override
    def current_temperature(self) -> float | None:
        """Return the measured domestic hot-water temperature."""
        value = self._value(_ACTUAL_TEMPERATURE)
        return float(value) if isinstance(value, int | float) else None

    @property
    @override
    def target_temperature(self) -> float | None:
        """Return the controller's active, read-only DHW setpoint."""
        value = self._value(_CURRENT_SETPOINT)
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
        """Return the native program, or read-only Bosch activity."""
        operation_to_value = self._operation_to_value()
        if operation_to_value:
            value = self._value(_OPERATION_MODE)
            if isinstance(value, str):
                value_to_operation = {
                    native.casefold(): operation
                    for operation, native in operation_to_value.items()
                }
                operation = value_to_operation.get(value.casefold())
                if operation is not None:
                    return operation
        status = self._value(_STATUS)
        if isinstance(status, str) and status.casefold() == "active":
            return "heating"
        if isinstance(status, str) and status.casefold() == "inactive":
            return "idle"
        return None

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose physical activity separately from the selected program."""
        return {
            "bosch_status": self._value(_STATUS),
            "target_is_read_only": True,
        }

    @override
    async def async_added_to_hass(self) -> None:
        """Register all compound Bosch inputs."""
        await super().async_added_to_hass()
        for path in (
            _ACTUAL_TEMPERATURE,
            _CURRENT_SETPOINT,
            _STATUS,
            _OPERATION_MODE,
        ):
            self.async_on_remove(self.coordinator.register_path(path))

    @override
    async def async_set_operation_mode(self, operation_mode: str) -> None:
        """Write the Bosch-native DHW operation mode."""
        operation_to_value = self._operation_to_value()
        if operation_mode not in operation_to_value:
            raise HomeAssistantError(f"Unsupported DHW program: {operation_mode}")
        await self.coordinator.async_write(
            _OPERATION_MODE, operation_to_value[operation_mode]
        )


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: BoschConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the discovered domestic hot-water circuit."""
    del hass
    runtime_data = config_entry.runtime_data
    async_add_entities([BoschDhwWaterHeater(runtime_data)])
