"""Text entities for Bosch Compress."""

from __future__ import annotations

from typing import override

from homeassistant.components.text import TextEntity, TextMode
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import BoschEntity
from .runtime import BoschConfigEntry, BoschRuntimeData

PARALLEL_UPDATES = 1


class BoschEndpointText(BoschEntity, TextEntity):
    """Represent an unbounded writable Bosch string endpoint."""

    _attr_mode = TextMode.TEXT
    _attr_native_min = 0
    _attr_native_max = 255

    def __init__(self, runtime_data: BoschRuntimeData, path: str) -> None:
        """Initialize a writable text setting."""
        super().__init__(runtime_data, path)
        self._attr_entity_category = EntityCategory.CONFIG

    @property
    @override
    def native_value(self) -> str | None:
        """Return the current string value."""
        value = self.endpoint.value
        return value if isinstance(value, str) else None

    @override
    async def async_set_value(self, value: str) -> None:
        """Write the string endpoint."""
        await self.coordinator.async_write(self._path, value)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: BoschConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up writable free-form Bosch string endpoints."""
    del hass
    runtime_data = config_entry.runtime_data
    async_add_entities(
        BoschEndpointText(runtime_data, path)
        for path, endpoint in runtime_data.coordinator.discovered_endpoints.items()
        if endpoint.writable
        and endpoint.value_type == "stringValue"
        and not endpoint.allowed_values
        and path != "/gateway/DateTime"
    )
