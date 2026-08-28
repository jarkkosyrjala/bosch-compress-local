"""Select entities for Bosch Compress."""

from __future__ import annotations

from typing import override

from homeassistant.components.select import DOMAIN as SELECT_DOMAIN
from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .entity import BoschEntity, H60Entity, is_binary_endpoint, string_values
from .h60 import H60_REGISTERS, H60RegisterDescription, H60RegisterKind
from .runtime import BoschConfigEntry, BoschRuntimeData

PARALLEL_UPDATES = 1

_DHW_OPERATION_MODE_PATH = "/dhwCircuits/dhw1/operationMode"
_H60_DHW_PROGRAM_REGISTER = "2213"
_DHW_PROGRAM_LABELS = {
    "off": "Off",
    "low": "Eco / Normal",
    "high": "Comfort",
    "ownprogram": "Manual time program",
    "eco": "Eco+",
}


def _native_dhw_program_unique_id(runtime_data: BoschRuntimeData) -> str:
    """Return the transport-neutral registry identity for the DHW program."""
    return f"{runtime_data.gateway_uuid}_dhw_program"


def _migrate_native_dhw_program_registry_entry(
    hass: HomeAssistant, runtime_data: BoschRuntimeData
) -> None:
    """Move the temporary H60 registry entry to the native identity safely."""
    registry = er.async_get(hass)
    legacy_unique_id = f"{runtime_data.gateway_uuid}_h60_{_H60_DHW_PROGRAM_REGISTER}"
    native_unique_id = _native_dhw_program_unique_id(runtime_data)
    legacy_entity_id = registry.async_get_entity_id(
        SELECT_DOMAIN, DOMAIN, legacy_unique_id
    )
    if legacy_entity_id is None or registry.async_get_entity_id(
        SELECT_DOMAIN, DOMAIN, native_unique_id
    ):
        return
    registry.async_update_entity(legacy_entity_id, new_unique_id=native_unique_id)


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


class BoschDhwProgramSelect(BoschEntity, SelectEntity):
    """Represent the Bosch controller's native DHW program."""

    def __init__(self, runtime_data: BoschRuntimeData) -> None:
        """Initialize the native DHW program select."""
        super().__init__(
            runtime_data,
            _DHW_OPERATION_MODE_PATH,
            name="DHW program",
            enabled_default=True,
        )
        self._attr_entity_category = EntityCategory.CONFIG
        self._attr_unique_id = _native_dhw_program_unique_id(runtime_data)
        self.internal_integration_suggested_object_id = "bosch_compress_dhw_program"
        endpoint = runtime_data.coordinator.discovered_endpoints[
            _DHW_OPERATION_MODE_PATH
        ]
        self._option_to_value = {
            _DHW_PROGRAM_LABELS.get(value.casefold(), value): value
            for value in string_values(endpoint.allowed_values)
        }
        self._value_to_option = {
            value.casefold(): option for option, value in self._option_to_value.items()
        }
        self._attr_options = list(self._option_to_value)

    @property
    @override
    def current_option(self) -> str | None:
        """Return the friendly label for the gateway's raw operation mode."""
        value = self.endpoint.value
        if not isinstance(value, str):
            return None
        return self._value_to_option.get(value.casefold())

    @override
    async def async_select_option(self, option: str) -> None:
        """Write the exact raw value advertised for a friendly label."""
        await self.coordinator.async_write(
            _DHW_OPERATION_MODE_PATH, self._option_to_value[option]
        )


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
    runtime_data = config_entry.runtime_data
    program_endpoint = runtime_data.coordinator.discovered_endpoints.get(
        _DHW_OPERATION_MODE_PATH
    )
    native_program_available = (
        program_endpoint is not None
        and program_endpoint.writable
        and program_endpoint.value_type == "stringValue"
        and bool(string_values(program_endpoint.allowed_values))
    )
    entities: list[SelectEntity] = []
    if native_program_available:
        _migrate_native_dhw_program_registry_entry(hass, runtime_data)
        entities.append(BoschDhwProgramSelect(runtime_data))
    entities.extend(
        BoschEndpointSelect(runtime_data, path)
        for path, endpoint in runtime_data.coordinator.discovered_endpoints.items()
        if path != _DHW_OPERATION_MODE_PATH
        and endpoint.writable
        and endpoint.value_type == "stringValue"
        and string_values(endpoint.allowed_values)
        and not is_binary_endpoint(endpoint)
    )
    if runtime_data.h60 is not None:
        entities.extend(
            H60SupplementalSelect(runtime_data, description)
            for description in H60_REGISTERS.values()
            if description.kind is H60RegisterKind.SELECT
            and not (
                native_program_available
                and description.register == _H60_DHW_PROGRAM_REGISTER
            )
        )
    async_add_entities(entities)
