"""Climate entities for Bosch Compress heating circuits."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar, Final, override

from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate.const import (
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import BoschEntity
from .runtime import BoschConfigEntry, BoschRuntimeData

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


_CIRCUITS: Final = (_circuit_paths("hc1"), _circuit_paths("hc2"))
_ACTUAL_MODULATION: Final = "/heatSources/actualModulation"


class BoschHeatingCircuitClimate(BoschEntity, ClimateEntity):
    """Represent one independent Bosch heating circuit."""

    _attr_hvac_modes: ClassVar[list[HVACMode]] = [HVACMode.AUTO, HVACMode.HEAT]
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = 0.5

    def __init__(
        self, runtime_data: BoschRuntimeData, paths: HeatingCircuitPaths
    ) -> None:
        """Initialize a heating-circuit climate entity."""
        super().__init__(
            runtime_data,
            paths.room_temperature,
            name=f"Heating Circuit {paths.circuit_id.removeprefix('hc')}",
            enabled_default=True,
        )
        self._paths = paths
        self._attr_unique_id = f"{runtime_data.gateway_uuid}_{paths.circuit_id}"
        self._attr_suggested_object_id = f"bosch_compress_{paths.circuit_id}"
        self._attr_min_temp = 13.0
        self._attr_max_temp = 29.0

    def _value(self, path: str) -> Any:
        endpoint = self.coordinator.endpoint(path)
        return endpoint.value if endpoint is not None else None

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
        """Advertise target control only while a verified transport can write it."""
        if self._native_target_path() is not None or self._runtime_data.h60 is not None:
            return ClimateEntityFeature.TARGET_TEMPERATURE
        return ClimateEntityFeature(0)

    @property
    @override
    def available(self) -> bool:
        """Return availability when the circuit's core endpoints are present."""
        return (
            super().available
            and self.coordinator.endpoint(self._paths.room_temperature) is not None
            and self.coordinator.endpoint(self._paths.current_room_setpoint) is not None
            and self.coordinator.endpoint(self._paths.operation_mode) is not None
        )

    @property
    @override
    def current_temperature(self) -> float | None:
        """Return measured room temperature, never the target register."""
        value = self._value(self._paths.room_temperature)
        return float(value) if isinstance(value, int | float) else None

    @property
    @override
    def target_temperature(self) -> float | None:
        """Return the current room target reported by Bosch."""
        value = self._value(self._paths.current_room_setpoint)
        return float(value) if isinstance(value, int | float) else None

    @property
    @override
    def hvac_mode(self) -> HVACMode | None:
        """Return the requested Bosch circuit operating mode."""
        return {
            "auto": HVACMode.AUTO,
            "manual": HVACMode.HEAT,
        }.get(self._value(self._paths.operation_mode))

    @property
    @override
    def hvac_action(self) -> HVACAction | None:
        """Return heating only when this circuit is circulating generated heat."""
        if self._value(self._paths.status) != "ACTIVE":
            return HVACAction.IDLE
        pump = self._value(self._paths.pump_modulation)
        source = self._value(_ACTUAL_MODULATION)
        if (
            isinstance(pump, int | float)
            and isinstance(source, int | float)
            and pump > 0
            and source > 0
        ):
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
            self._paths.current_room_setpoint,
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
            if path in self.coordinator.discovered_endpoints:
                self.async_on_remove(self.coordinator.register_path(path))
        if self._runtime_data.h60 is not None:
            self.async_on_remove(
                self._runtime_data.h60.add_listener(self._handle_h60_update)
            )

    @callback
    def _handle_h60_update(self, register: str, value: int | float) -> None:
        """Refresh HC1 after its supplemental target register changes."""
        del value
        circuit_registers = {
            "hc1": {"0203", "020C"},
            "hc2": {"0220"},
        }
        if register in circuit_registers[self._paths.circuit_id]:
            self.async_write_ha_state()

    @override
    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Write the native Bosch automatic/manual mode."""
        value = {HVACMode.AUTO: "auto", HVACMode.HEAT: "manual"}.get(hvac_mode)
        if value is None:
            raise HomeAssistantError(f"Unsupported HVAC mode: {hvac_mode}")
        await self.coordinator.async_write(self._paths.operation_mode, value)

    @override
    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Write the active room target, preferring the native Bosch endpoint."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if isinstance(temperature, bool) or not isinstance(temperature, int | float):
            raise HomeAssistantError("A numeric target temperature is required")

        if (native_path := self._native_target_path()) is not None:
            await self.coordinator.async_write(native_path, float(temperature))
            return

        if self._runtime_data.h60 is None:
            raise HomeAssistantError(
                "This heating circuit does not expose a verified writable room target"
            )
        if self._paths.circuit_id == "hc2":
            register = "0220"
        elif self.hvac_mode is HVACMode.AUTO:
            register = "020C"
        else:
            register = "0203"
        await self._runtime_data.h60.async_set_value(register, float(temperature))


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: BoschConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up every discovered heating circuit independently."""
    del hass
    runtime_data = config_entry.runtime_data
    discovered = runtime_data.coordinator.discovered_endpoints
    async_add_entities(
        BoschHeatingCircuitClimate(runtime_data, paths)
        for paths in _CIRCUITS
        if paths.room_temperature in discovered
        and paths.current_room_setpoint in discovered
        and paths.operation_mode in discovered
    )
