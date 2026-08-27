"""Switch entities for Bosch Compress."""

from __future__ import annotations

from typing import Any, override

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import BoschEntity, H60Entity, is_binary_endpoint
from .h60 import H60_REGISTERS, H60RegisterDescription, H60RegisterKind
from .runtime import BoschConfigEntry, BoschRuntimeData

PARALLEL_UPDATES = 1


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
    del hass
    runtime_data = config_entry.runtime_data
    entities: list[SwitchEntity] = [
        BoschEndpointSwitch(runtime_data, path)
        for path, endpoint in runtime_data.coordinator.discovered_endpoints.items()
        if endpoint.writable and is_binary_endpoint(endpoint)
    ]
    if runtime_data.h60 is not None:
        entities.extend(
            H60SupplementalSwitch(runtime_data, description)
            for description in H60_REGISTERS.values()
            if description.kind is H60RegisterKind.SWITCH
        )
    async_add_entities(entities)
