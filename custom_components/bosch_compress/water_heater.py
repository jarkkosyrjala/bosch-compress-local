"""Domestic hot-water entity for Bosch Compress."""

from __future__ import annotations

from typing import Any, override

from homeassistant.components.water_heater import (
    WaterHeaterEntity,
    WaterHeaterEntityFeature,
)
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import BoschEntity
from .h60 import H60_REGISTERS
from .runtime import BoschConfigEntry, BoschRuntimeData

PARALLEL_UPDATES = 1

_ACTUAL_TEMPERATURE = "/dhwCircuits/dhw1/actualTemp"
_CURRENT_SETPOINT = "/dhwCircuits/dhw1/currentSetpoint"
_STATUS = "/dhwCircuits/dhw1/status"
_DHW_PROGRAM_REGISTER = "2213"


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
        self._program = H60_REGISTERS[_DHW_PROGRAM_REGISTER]
        self._value_to_operation = dict(self._program.options)
        self._operation_to_value = {
            label: value for value, label in self._program.options
        }
        self._attr_supported_features = WaterHeaterEntityFeature(0)
        if runtime_data.h60 is not None:
            self._attr_supported_features |= WaterHeaterEntityFeature.OPERATION_MODE
            self._attr_operation_list = list(self._operation_to_value)

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
    def current_operation(self) -> str | None:
        """Return the H60 program, or Bosch activity when H60 is absent."""
        if self._runtime_data.h60 is not None:
            value = self._runtime_data.h60.value(_DHW_PROGRAM_REGISTER)
            return self._value_to_operation.get(value) if value is not None else None
        status = self._value(_STATUS)
        if status == "ACTIVE":
            return "heating"
        if status == "INACTIVE":
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
        """Register all compound Bosch and H60 inputs."""
        await super().async_added_to_hass()
        self.async_on_remove(self.coordinator.register_path(_CURRENT_SETPOINT))
        self.async_on_remove(self.coordinator.register_path(_STATUS))
        if self._runtime_data.h60 is not None:
            self.async_on_remove(
                self._runtime_data.h60.add_listener(self._handle_h60_update)
            )

    @callback
    def _handle_h60_update(self, register: str, value: int | float) -> None:
        """Refresh when the selected hot-water program changes."""
        del value
        if register == _DHW_PROGRAM_REGISTER:
            self.async_write_ha_state()

    @override
    async def async_set_operation_mode(self, operation_mode: str) -> None:
        """Write the exact numeric H60 DHW program value."""
        h60 = self._runtime_data.h60
        if h60 is None or operation_mode not in self._operation_to_value:
            raise HomeAssistantError(f"Unsupported DHW program: {operation_mode}")
        await h60.async_set_value(
            _DHW_PROGRAM_REGISTER,
            self._operation_to_value[operation_mode],
        )


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: BoschConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the discovered domestic hot-water circuit."""
    del hass
    runtime_data = config_entry.runtime_data
    discovered = runtime_data.coordinator.discovered_endpoints
    if {
        _ACTUAL_TEMPERATURE,
        _CURRENT_SETPOINT,
        _STATUS,
    }.issubset(discovered):
        async_add_entities([BoschDhwWaterHeater(runtime_data)])
