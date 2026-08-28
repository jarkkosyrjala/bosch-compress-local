"""Select entities for Bosch Compress."""

from __future__ import annotations

from typing import override

from homeassistant.components.select import DOMAIN as SELECT_DOMAIN
from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .entity import BoschEntity, is_binary_endpoint, string_values
from .runtime import BoschConfigEntry, BoschRuntimeData

PARALLEL_UPDATES = 1

_DHW_CHARGE_PATH = "/dhwCircuits/dhw1/charge"
_DHW_OPERATION_MODE_PATH = "/dhwCircuits/dhw1/operationMode"
_H60_DHW_PROGRAM_REGISTER = "2213"
_DHW_PROGRAM_LABELS = {
    "off": "Off",
    "low": "Eco / Normal",
    "high": "Comfort",
    "ownprogram": "Manual time program",
    "eco": "Eco+",
}


def _dhw_program_option_map(endpoint: object | None) -> dict[str, str]:
    """Map friendly options to exact native values for a usable endpoint."""
    if (
        endpoint is None
        or not getattr(endpoint, "writable", False)
        or getattr(endpoint, "value_type", None) != "stringValue"
    ):
        return {}
    return {
        _DHW_PROGRAM_LABELS.get(value.casefold(), value): value
        for value in string_values(getattr(endpoint, "allowed_values", ()))
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
    if legacy_entity_id is None:
        return
    native_entity_id = registry.async_get_entity_id(
        SELECT_DOMAIN, DOMAIN, native_unique_id
    )
    if native_entity_id is not None:
        registry.async_remove(native_entity_id)
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

    @property
    @override
    def available(self) -> bool:
        """Return availability after the writable native endpoint appears."""
        return super().available and bool(_dhw_program_option_map(self.endpoint))

    @property
    @override
    def options(self) -> list[str]:
        """Return friendly options derived from current endpoint metadata."""
        return list(_dhw_program_option_map(self.endpoint))

    @property
    @override
    def current_option(self) -> str | None:
        """Return the friendly label for the gateway's raw operation mode."""
        endpoint = self.endpoint
        if endpoint is None:
            return None
        value = endpoint.value
        if not isinstance(value, str):
            return None
        value_to_option = {
            native.casefold(): option
            for option, native in _dhw_program_option_map(endpoint).items()
        }
        return value_to_option.get(value.casefold())

    @override
    async def async_select_option(self, option: str) -> None:
        """Write the exact raw value advertised for a friendly label."""
        option_to_value = _dhw_program_option_map(self.endpoint)
        if option not in option_to_value:
            raise HomeAssistantError(f"Unsupported DHW program: {option}")
        await self.coordinator.async_write(
            _DHW_OPERATION_MODE_PATH, option_to_value[option]
        )

    @override
    async def async_added_to_hass(self) -> None:
        """Retry the fixed operation-mode path until it becomes available."""
        await super().async_added_to_hass()
        self.async_on_remove(self.coordinator.register_path(_DHW_OPERATION_MODE_PATH))


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: BoschConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up writable Bosch enumerations."""
    runtime_data = config_entry.runtime_data
    _migrate_native_dhw_program_registry_entry(hass, runtime_data)
    entities: list[SelectEntity] = [BoschDhwProgramSelect(runtime_data)]
    entities.extend(
        BoschEndpointSelect(runtime_data, path)
        for path, endpoint in runtime_data.coordinator.discovered_endpoints.items()
        if path not in {_DHW_CHARGE_PATH, _DHW_OPERATION_MODE_PATH}
        and endpoint.writable
        and endpoint.value_type == "stringValue"
        and string_values(endpoint.allowed_values)
        and not is_binary_endpoint(endpoint)
    )
    async_add_entities(entities)
