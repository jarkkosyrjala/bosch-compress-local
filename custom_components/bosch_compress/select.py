"""Select entities for Bosch Compress."""

from __future__ import annotations

from typing import override

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import BoschEntity, is_binary_endpoint, string_values
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
    """Return one control path for a domestic-hot-water circuit."""
    return f"/dhwCircuits/{circuit_id}/{endpoint_name}"


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


def _native_dhw_program_unique_id(
    runtime_data: BoschRuntimeData, circuit_id: str = "dhw1"
) -> str:
    """Return the transport-neutral registry identity for the DHW program."""
    suffix = "dhw_program" if circuit_id == "dhw1" else f"{circuit_id}_program"
    return f"{runtime_data.gateway_uuid}_{suffix}"


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

    def __init__(
        self, runtime_data: BoschRuntimeData, circuit_id: str = "dhw1"
    ) -> None:
        """Initialize the native DHW program select."""
        path = _dhw_path(circuit_id, "operationMode")
        suffix = "" if circuit_id == "dhw1" else f" {circuit_id.removeprefix('dhw')}"
        super().__init__(
            runtime_data,
            path,
            name=f"DHW program{suffix}",
            enabled_default=True,
        )
        self._attr_entity_category = EntityCategory.CONFIG
        self._attr_unique_id = _native_dhw_program_unique_id(runtime_data, circuit_id)

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
        await self.coordinator.async_write(self._path, option_to_value[option])

    @override
    async def async_added_to_hass(self) -> None:
        """Retry the fixed operation-mode path until it becomes available."""
        await super().async_added_to_hass()
        self.async_on_remove(self.coordinator.register_path(self._path))


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: BoschConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up writable Bosch enumerations."""
    del hass
    runtime_data = config_entry.runtime_data
    topology = effective_topology(runtime_data)
    control_paths = {
        _dhw_path(circuit_id, endpoint_name)
        for circuit_id in topology.dhw_charge_circuits | topology.dhw_program_circuits
        for endpoint_name in ("charge", "operationMode")
    }
    entities: list[SelectEntity] = [
        BoschDhwProgramSelect(runtime_data, circuit_id)
        for circuit_id in sorted(topology.dhw_program_circuits)
    ]
    entities.extend(
        BoschEndpointSelect(runtime_data, path)
        for path, endpoint in runtime_data.coordinator.discovered_endpoints.items()
        if path not in control_paths
        and endpoint.writable
        and endpoint.value_type == "stringValue"
        and string_values(endpoint.allowed_values)
        and not is_binary_endpoint(endpoint)
    )
    async_add_entities(entities)
