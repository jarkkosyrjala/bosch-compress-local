"""Select entities for Bosch Compress."""

from __future__ import annotations

from typing import override

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import BoschEntity, H60Entity, is_binary_endpoint, string_values
from .h60 import H60_REGISTERS, H60RegisterDescription, H60RegisterKind
from .runtime import BoschConfigEntry, BoschRuntimeData

PARALLEL_UPDATES = 1


class BoschEndpointSelect(BoschEntity, SelectEntity):
    """Represent a writable Bosch enumeration."""

    def __init__(self, runtime_data: BoschRuntimeData, path: str) -> None:
        """Initialize a writable select."""
        super().__init__(runtime_data, path, diagnostic=True)
        self._attr_entity_category = EntityCategory.CONFIG
        endpoint = runtime_data.coordinator.discovered_endpoints[path]
        self._attr_options = list(string_values(endpoint.allowed_values))

    @property
    @override
    def current_option(self) -> str | None:
        """Return the selected option."""
        value = self.endpoint.value
        return value if isinstance(value, str) and value in self.options else None

    @override
    async def async_select_option(self, option: str) -> None:
        """Select and write an endpoint option."""
        await self.coordinator.async_write(self._path, option)


class H60SupplementalSelect(H60Entity, SelectEntity):
    """Represent an H60 numeric enumeration as a native select."""

    def __init__(
        self,
        runtime_data: BoschRuntimeData,
        description: H60RegisterDescription,
    ) -> None:
        """Initialize an H60 select."""
        super().__init__(runtime_data, description)
        self._value_to_option = dict(description.options)
        self._option_to_value = {option: value for value, option in description.options}
        self._attr_options = list(self._option_to_value)

    @property
    @override
    def current_option(self) -> str | None:
        """Return the label corresponding to the raw H60 value."""
        value = self.h60_value
        return self._value_to_option.get(value) if value is not None else None

    @override
    async def async_select_option(self, option: str) -> None:
        """Publish the exact numeric value for the selected label."""
        if self._runtime_data.h60 is not None:
            await self._runtime_data.h60.async_set_value(
                self._description.register, self._option_to_value[option]
            )


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: BoschConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up writable Bosch enumerations."""
    del hass
    runtime_data = config_entry.runtime_data
    entities: list[SelectEntity] = [
        BoschEndpointSelect(runtime_data, path)
        for path, endpoint in runtime_data.coordinator.discovered_endpoints.items()
        if endpoint.writable
        and endpoint.value_type == "stringValue"
        and string_values(endpoint.allowed_values)
        and not is_binary_endpoint(endpoint)
    ]
    if runtime_data.h60 is not None:
        entities.extend(
            H60SupplementalSelect(runtime_data, description)
            for description in H60_REGISTERS.values()
            if description.kind is H60RegisterKind.SELECT
        )
    async_add_entities(entities)
