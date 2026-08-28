"""Switch entities for Bosch Compress."""

from __future__ import annotations

from typing import Any, override

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import BoschEntity, is_binary_endpoint
from .runtime import BoschConfigEntry, BoschRuntimeData
from .topology import effective_topology

PARALLEL_UPDATES = 1


def _dhw_charge_path(circuit_id: str) -> str:
    """Return the native single-charge path for one DHW circuit."""
    return f"/dhwCircuits/{circuit_id}/charge"


def _native_charge_available(endpoint: object | None) -> bool:
    """Return whether the fixed charge endpoint supports start and stop."""
    if endpoint is None or not getattr(endpoint, "writable", False):
        return False
    values = {
        value.casefold()
        for value in getattr(endpoint, "allowed_values", ())
        if isinstance(value, str)
    }
    return {"start", "stop"}.issubset(values)


def _native_charge_unique_id(
    runtime_data: BoschRuntimeData, circuit_id: str = "dhw1"
) -> str:
    """Return the transport-neutral registry identity for native DHW charge."""
    suffix = (
        "extra_warm_water" if circuit_id == "dhw1" else f"{circuit_id}_extra_warm_water"
    )
    return f"{runtime_data.gateway_uuid}_{suffix}"


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

    def __init__(
        self, runtime_data: BoschRuntimeData, circuit_id: str = "dhw1"
    ) -> None:
        """Initialize the native extra-warm-water switch."""
        path = _dhw_charge_path(circuit_id)
        suffix = "" if circuit_id == "dhw1" else f" {circuit_id.removeprefix('dhw')}"
        super().__init__(
            runtime_data,
            path,
            name=f"Extra warm water{suffix}",
            enabled_default=True,
        )
        self._attr_unique_id = _native_charge_unique_id(runtime_data, circuit_id)

    @property
    @override
    def available(self) -> bool:
        """Return availability after the writable native endpoint appears."""
        return super().available and _native_charge_available(self.endpoint)

    @property
    @override
    def is_on(self) -> bool | None:
        """Return whether a native DHW single charge is active."""
        endpoint = self.endpoint
        if endpoint is None:
            return None
        value = endpoint.value
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
        endpoint = self.endpoint
        if not _native_charge_available(endpoint):
            raise HomeAssistantError("Native extra warm water control is unavailable")
        assert endpoint is not None
        for value in endpoint.allowed_values:
            if isinstance(value, str) and value.casefold() == requested:
                return value
        return requested

    @override
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Start a native DHW single charge."""
        del kwargs
        await self.coordinator.async_write(self._path, self._write_value("start"))

    @override
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Stop a native DHW single charge."""
        del kwargs
        await self.coordinator.async_write(self._path, self._write_value("stop"))

    @override
    async def async_added_to_hass(self) -> None:
        """Retry the fixed charge path until it becomes available."""
        await super().async_added_to_hass()
        self.async_on_remove(self.coordinator.register_path(self._path))


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: BoschConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up writable Bosch boolean endpoints."""
    del hass
    runtime_data = config_entry.runtime_data
    topology = effective_topology(runtime_data)
    charge_paths = {
        _dhw_charge_path(circuit_id) for circuit_id in topology.dhw_charge_circuits
    }
    entities: list[SwitchEntity] = [
        BoschExtraWarmWaterSwitch(runtime_data, circuit_id)
        for circuit_id in sorted(topology.dhw_charge_circuits)
    ]
    entities.extend(
        BoschEndpointSwitch(runtime_data, path)
        for path, endpoint in runtime_data.coordinator.discovered_endpoints.items()
        if path not in charge_paths
        and endpoint.writable
        and is_binary_endpoint(endpoint)
    )
    async_add_entities(entities)
