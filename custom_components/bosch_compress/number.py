"""Number entities for Bosch Compress."""

from __future__ import annotations

from typing import override

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import BoschEntity, is_default_enabled_endpoint
from .runtime import BoschConfigEntry, BoschRuntimeData

PARALLEL_UPDATES = 1


class BoschEndpointNumber(BoschEntity, NumberEntity):
    """Represent a writable numeric Bosch endpoint."""

    _attr_mode = NumberMode.BOX

    def __init__(self, runtime_data: BoschRuntimeData, path: str) -> None:
        """Initialize a writable number."""
        super().__init__(
            runtime_data,
            path,
            enabled_default=is_default_enabled_endpoint(path),
            diagnostic=not is_default_enabled_endpoint(path),
        )
        endpoint = runtime_data.coordinator.discovered_endpoints[path]
        if not is_default_enabled_endpoint(path):
            self._attr_entity_category = EntityCategory.CONFIG
        self._attr_native_min_value = endpoint.minimum
        self._attr_native_max_value = endpoint.maximum
        if endpoint.unit in {"C", "°C"}:
            self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
            self._attr_native_step = 0.5
        elif endpoint.minimum is not None and endpoint.maximum is not None:
            self._attr_native_step = (
                0.1
                if any(
                    float(value) != int(float(value))
                    for value in (endpoint.minimum, endpoint.maximum)
                )
                else 1.0
            )

    @property
    @override
    def native_value(self) -> float | None:
        """Return the current numeric value."""
        value = self.endpoint.value
        if isinstance(value, int | float) and not isinstance(value, bool):
            return float(value)
        return None

    @override
    async def async_set_native_value(self, value: float) -> None:
        """Write a numeric endpoint value."""
        await self.coordinator.async_write(self._path, value)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: BoschConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up writable Bosch numeric endpoints."""
    del hass
    runtime_data = config_entry.runtime_data
    entities: list[NumberEntity] = [
        BoschEndpointNumber(runtime_data, path)
        for path, endpoint in runtime_data.coordinator.discovered_endpoints.items()
        if endpoint.writable
        and endpoint.value_type == "floatValue"
        and endpoint.minimum is not None
        and endpoint.maximum is not None
    ]
    async_add_entities(entities)
