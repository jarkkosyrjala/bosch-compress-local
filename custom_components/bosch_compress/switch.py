"""Switch entities for Bosch Compress."""

from __future__ import annotations

from typing import Any, override

from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .entity import BoschEntity, H60Entity, is_binary_endpoint
from .h60 import H60_REGISTERS, H60RegisterDescription, H60RegisterKind
from .runtime import BoschConfigEntry, BoschRuntimeData

PARALLEL_UPDATES = 1

_DHW_CHARGE_PATH = "/dhwCircuits/dhw1/charge"
_H60_DHW_CHARGE_REGISTER = "1231"


def _native_charge_unique_id(runtime_data: BoschRuntimeData) -> str:
    """Return the transport-neutral registry identity for native DHW charge."""
    return f"{runtime_data.gateway_uuid}_extra_warm_water"


def _migrate_native_charge_registry_entry(
    hass: HomeAssistant, runtime_data: BoschRuntimeData
) -> None:
    """Move the temporary H60 registry entry to the native identity safely."""
    registry = er.async_get(hass)
    legacy_unique_id = f"{runtime_data.gateway_uuid}_h60_{_H60_DHW_CHARGE_REGISTER}"
    native_unique_id = _native_charge_unique_id(runtime_data)
    legacy_entity_id = registry.async_get_entity_id(
        SWITCH_DOMAIN, DOMAIN, legacy_unique_id
    )
    if legacy_entity_id is None or registry.async_get_entity_id(
        SWITCH_DOMAIN, DOMAIN, native_unique_id
    ):
        return
    registry.async_update_entity(legacy_entity_id, new_unique_id=native_unique_id)


def _binary_write_value(endpoint: Any, turn_on: bool) -> bool | str:
    """Return the endpoint's native representation of a boolean."""
    if isinstance(endpoint.value, bool):
        return turn_on
    wanted = {"on", "true"} if turn_on else {"off", "false"}
    for value in endpoint.allowed_values:
        if isinstance(value, str) and value.casefold() in wanted:
            return value
    return "on" if turn_on else "off"


class BoschEndpointSwitch(BoschEntity, SwitchEntity):
    """Represent a writable Bosch boolean endpoint."""

    def __init__(self, runtime_data: BoschRuntimeData, path: str) -> None:
        """Initialize a writable switch."""
        super().__init__(runtime_data, path, diagnostic=True)
        self._attr_entity_category = EntityCategory.CONFIG

    @property
    @override
    def is_on(self) -> bool | None:
        """Return the endpoint's boolean state."""
        value = self.endpoint.value
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.casefold()
            if normalized in {"on", "true"}:
                return True
            if normalized in {"off", "false"}:
                return False
        return None

    @override
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the endpoint on."""
        del kwargs
        await self.coordinator.async_write(
            self._path, _binary_write_value(self.endpoint, True)
        )

    @override
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the endpoint off."""
        del kwargs
        await self.coordinator.async_write(
            self._path, _binary_write_value(self.endpoint, False)
        )


class BoschExtraWarmWaterSwitch(BoschEntity, SwitchEntity):
    """Start or stop the Bosch controller's native DHW single charge."""

    def __init__(self, runtime_data: BoschRuntimeData) -> None:
        """Initialize the native extra-warm-water switch."""
        super().__init__(
            runtime_data,
            _DHW_CHARGE_PATH,
            name="Extra warm water",
            enabled_default=True,
        )
        self._attr_unique_id = _native_charge_unique_id(runtime_data)
        self.internal_integration_suggested_object_id = (
            "bosch_compress_extra_warm_water"
        )

    @property
    @override
    def is_on(self) -> bool | None:
        """Return whether a native DHW single charge is active."""
        value = self.endpoint.value
        if not isinstance(value, str):
            return None
        normalized = value.casefold()
        if normalized == "start":
            return True
        if normalized == "stop":
            return False
        return None

    def _write_value(self, requested: str) -> str:
        """Return the exact casing advertised by this gateway, when available."""
        for value in self.endpoint.allowed_values:
            if isinstance(value, str) and value.casefold() == requested:
                return value
        return requested

    @override
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Start a native DHW single charge."""
        del kwargs
        await self.coordinator.async_write(_DHW_CHARGE_PATH, self._write_value("start"))

    @override
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Stop a native DHW single charge."""
        del kwargs
        await self.coordinator.async_write(_DHW_CHARGE_PATH, self._write_value("stop"))


class H60SupplementalSwitch(H60Entity, SwitchEntity):
    """Represent an H60-only writable binary control."""

    def __init__(
        self,
        runtime_data: BoschRuntimeData,
        description: H60RegisterDescription,
    ) -> None:
        """Initialize an H60 switch."""
        super().__init__(runtime_data, description)

    @property
    @override
    def is_on(self) -> bool | None:
        """Return the latest H60 switch state."""
        value = self.h60_value
        return value != 0 if value is not None else None

    @override
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Publish the verified H60 on payload."""
        del kwargs
        if self._runtime_data.h60 is not None:
            await self._runtime_data.h60.async_set_value(self._description.register, 1)

    @override
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Publish the verified H60 off payload."""
        del kwargs
        if self._runtime_data.h60 is not None:
            await self._runtime_data.h60.async_set_value(self._description.register, 0)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: BoschConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up writable Bosch boolean endpoints."""
    runtime_data = config_entry.runtime_data
    charge_endpoint = runtime_data.coordinator.discovered_endpoints.get(
        _DHW_CHARGE_PATH
    )
    charge_values = (
        {
            value.casefold()
            for value in charge_endpoint.allowed_values
            if isinstance(value, str)
        }
        if charge_endpoint is not None
        else set()
    )
    native_charge_available = (
        charge_endpoint is not None
        and charge_endpoint.writable
        and {"start", "stop"}.issubset(charge_values)
    )
    entities: list[SwitchEntity] = []
    if native_charge_available:
        _migrate_native_charge_registry_entry(hass, runtime_data)
        entities.append(BoschExtraWarmWaterSwitch(runtime_data))
    entities.extend(
        BoschEndpointSwitch(runtime_data, path)
        for path, endpoint in runtime_data.coordinator.discovered_endpoints.items()
        if path != _DHW_CHARGE_PATH
        and endpoint.writable
        and is_binary_endpoint(endpoint)
    )
    if runtime_data.h60 is not None:
        entities.extend(
            H60SupplementalSwitch(runtime_data, description)
            for description in H60_REGISTERS.values()
            if description.kind is H60RegisterKind.SWITCH
            and not (
                native_charge_available
                and description.register == _H60_DHW_CHARGE_REGISTER
            )
        )
    async_add_entities(entities)
