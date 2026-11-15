"""Climate entities for Bosch Compress heating circuits."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final, override

from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate.const import (
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import BoschEntity
from .runtime import BoschConfigEntry, BoschRuntimeData
from .topology import effective_topology

PARALLEL_UPDATES = 1


@dataclass(frozen=True, slots=True)
class HeatingCircuitPaths:
    """Endpoint paths used by one heating circuit."""

    circuit_id: str
    room_temperature: str
    current_room_setpoint: str
    manual_room_setpoint: str
    temporary_room_setpoint: str
    temperature_room_setpoint: str
    actual_supply_temperature: str
    supply_temperature_setpoint: str
    operation_mode: str
    pump_modulation: str
    status: str


def _circuit_paths(circuit_id: str) -> HeatingCircuitPaths:
    base = f"/heatingCircuits/{circuit_id}"
    return HeatingCircuitPaths(
        circuit_id=circuit_id,
        room_temperature=f"{base}/roomtemperature",
        current_room_setpoint=f"{base}/currentRoomSetpoint",
        manual_room_setpoint=f"{base}/manualRoomSetpoint",
        temporary_room_setpoint=f"{base}/temporaryRoomSetpoint",
        temperature_room_setpoint=f"{base}/temperatureRoomSetpoint",
        actual_supply_temperature=f"{base}/actualSupplyTemperature",
        supply_temperature_setpoint=f"{base}/supplyTemperatureSetpoint",
        operation_mode=f"{base}/operationMode",
        pump_modulation=f"{base}/pumpModulation",
        status=f"{base}/status",
    )


_ACTUAL_MODULATION: Final = "/heatSources/actualModulation"
_HVAC_MODE_VALUES: Final = {
    HVACMode.AUTO: "auto",
    HVACMode.HEAT: "manual",
}


class BoschHeatingCircuitClimate(BoschEntity, ClimateEntity):
    """Represent one independent Bosch heating circuit."""

    _attr_native_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = 0.5

    def __init__(
        self, runtime_data: BoschRuntimeData, paths: HeatingCircuitPaths
    ) -> None:
        """Initialize a heating-circuit climate entity."""
        super().__init__(
            runtime_data,
            paths.current_room_setpoint,
            name=f"Heating circuit {paths.circuit_id.removeprefix('hc')}",
            enabled_default=True,
        )
        self._paths = paths
        self._attr_unique_id = f"{runtime_data.gateway_uuid}_{paths.circuit_id}"
        self._attr_min_temp = 13.0
        self._attr_max_temp = 29.0

    def _value(self, path: str) -> Any:
        endpoint = self.coordinator.endpoint(path)
        return endpoint.value if endpoint is not None else None

    def _hvac_mode_value_map(self) -> dict[HVACMode, str]:
        """Return exact writable Bosch values for supported HVAC modes."""
        endpoint = self.coordinator.endpoint(self._paths.operation_mode)
        if (
            endpoint is None
            or not endpoint.writable
            or endpoint.value_type != "stringValue"
        ):
            return {}
        allowed_values = {
            value.casefold(): value
            for value in endpoint.allowed_values
            if isinstance(value, str)
        }
        return {
            mode: allowed_values[value]
            for mode, value in _HVAC_MODE_VALUES.items()
            if value in allowed_values
        }

    def _native_target_path(self) -> str | None:
        """Return the writable Bosch target appropriate for the current mode."""
        mode_path = {
            HVACMode.AUTO: self._paths.temporary_room_setpoint,
            HVACMode.HEAT: self._paths.manual_room_setpoint,
        }.get(self.hvac_mode)
        if mode_path is None:
            return None

        endpoint = self.coordinator.endpoint(mode_path)
        if endpoint is not None and endpoint.writable:
            return mode_path

        fallback = self.coordinator.endpoint(self._paths.temperature_room_setpoint)
        if fallback is not None and fallback.writable:
            return self._paths.temperature_room_setpoint
        return None

    @property
    @override
    def supported_features(self) -> ClimateEntityFeature:
        """Advertise target control only for a verified writable Bosch endpoint."""
        if self._native_target_path() is not None:
            return ClimateEntityFeature.TARGET_TEMPERATURE
        return ClimateEntityFeature(0)

    @property
    @override
    def available(self) -> bool:
        """Return availability when the circuit's core endpoints are present."""
        return (
            super().available
            and self.coordinator.endpoint(self._paths.operation_mode) is not None
        )

    @property
    @override
    def native_current_temperature(self) -> float | None:
        """Return measured room temperature, never the target register."""
        value = self._value(self._paths.room_temperature)
        return float(value) if isinstance(value, int | float) else None

    @property
    @override
    def native_target_temperature(self) -> float | None:
        """Return the current room target reported by Bosch."""
        value = self._value(self._paths.current_room_setpoint)
        return float(value) if isinstance(value, int | float) else None

    @property
    @override
    def hvac_modes(self) -> list[HVACMode]:
        """Return only HVAC modes explicitly writable through Bosch metadata."""
        return list(self._hvac_mode_value_map())

    @property
    @override
    def hvac_mode(self) -> HVACMode | None:
        """Return the requested Bosch circuit operating mode."""
        value = self._value(self._paths.operation_mode)
        if not isinstance(value, str):
            return None
        return {native: mode for mode, native in _HVAC_MODE_VALUES.items()}.get(
            value.casefold()
        )

    @property
    @override
    def hvac_action(self) -> HVACAction | None:
        """Return heating only when this circuit is circulating generated heat."""
        status = self._value(self._paths.status)
        if not isinstance(status, str):
            return None
        normalized_status = status.casefold()
        if normalized_status == "inactive":
            return HVACAction.IDLE
        if normalized_status != "active":
            return None

        pump = self._value(self._paths.pump_modulation)
        source = self._value(_ACTUAL_MODULATION)
        if (
            isinstance(pump, bool)
            or not isinstance(pump, int | float)
            or isinstance(source, bool)
            or not isinstance(source, int | float)
        ):
            return None
        if pump > 0 and source > 0:
            return HVACAction.HEATING
        return HVACAction.IDLE

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose useful circuit temperatures without overloading climate semantics."""
        return {
            "actual_supply_temperature": self._value(
                self._paths.actual_supply_temperature
            ),
            "supply_temperature_setpoint": self._value(
                self._paths.supply_temperature_setpoint
            ),
            "pump_modulation": self._value(self._paths.pump_modulation),
            "circuit_status": self._value(self._paths.status),
        }

    @override
    async def async_added_to_hass(self) -> None:
        """Register all endpoints used by the compound climate entity."""
        await super().async_added_to_hass()
        for path in (
            self._paths.room_temperature,
            self._paths.actual_supply_temperature,
            self._paths.supply_temperature_setpoint,
            self._paths.operation_mode,
            self._paths.pump_modulation,
            self._paths.status,
            _ACTUAL_MODULATION,
        ):
            self.async_on_remove(self.coordinator.register_path(path))
        for path in (
            self._paths.manual_room_setpoint,
            self._paths.temporary_room_setpoint,
            self._paths.temperature_room_setpoint,
        ):
            self.async_on_remove(self.coordinator.register_path(path))

    @override
    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Write the native Bosch automatic/manual mode."""
        value = self._hvac_mode_value_map().get(hvac_mode)
        if value is None:
            raise HomeAssistantError(f"Unsupported HVAC mode: {hvac_mode}")
        await self.coordinator.async_write(self._paths.operation_mode, value)

    @override
    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Write Celsius supplied by HA after its user-unit service conversion."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if isinstance(temperature, bool) or not isinstance(temperature, int | float):
            raise HomeAssistantError("A numeric target temperature is required")

        if (native_path := self._native_target_path()) is None:
            raise HomeAssistantError(
                "This heating circuit does not expose a verified writable room target"
            )
        await self.coordinator.async_write(native_path, float(temperature))


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: BoschConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up every discovered or previously observed heating circuit."""
    del hass
    runtime_data = config_entry.runtime_data
    async_add_entities(
        BoschHeatingCircuitClimate(runtime_data, _circuit_paths(circuit_id))
        for circuit_id in sorted(effective_topology(runtime_data).heating_circuits)
    )
