"""Shared entity helpers for Bosch Compress."""

from __future__ import annotations

import re
from collections.abc import Collection
from typing import Any, Final

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity, EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DEFAULT_NAME, DOMAIN
from .coordinator import BoschDataUpdateCoordinator
from .h60 import H60RegisterDescription
from .runtime import BoschRuntimeData

_NON_ALPHANUMERIC = re.compile(r"[^a-z0-9]+")

DEFAULT_ENABLED_ENDPOINTS: Final = frozenset(
    {
        "/dhwCircuits/dhw1/actualTemp",
        "/dhwCircuits/dhw1/currentSetpoint",
        "/dhwCircuits/dhw1/singleChargeSetpoint",
        "/heatSources/actualModulation",
        "/heatSources/applianceSupplyTemperature",
        "/heatSources/returnTemperature",
        "/heatingCircuits/hc1/actualSupplyTemperature",
        "/heatingCircuits/hc1/currentRoomSetpoint",
        "/heatingCircuits/hc1/roomtemperature",
        "/heatingCircuits/hc1/supplyTemperatureSetpoint",
        "/heatingCircuits/hc2/actualSupplyTemperature",
        "/heatingCircuits/hc2/currentRoomSetpoint",
        "/heatingCircuits/hc2/roomtemperature",
        "/heatingCircuits/hc2/supplyTemperatureSetpoint",
        "/notifications",
        "/system/appliance/actualSupplyTemperature",
        "/system/healthStatus",
        "/system/sensors/temperatures/outdoor_t1",
    }
)

_BINARY_VALUE_SETS: Final = (
    frozenset({"off", "on"}),
    frozenset({"false", "true"}),
)


def endpoint_object_id(path: str) -> str:
    """Build a deterministic object-id fragment from an endpoint path."""
    return _NON_ALPHANUMERIC.sub("_", path.casefold()).strip("_")


def endpoint_name(path: str) -> str:
    """Build a readable endpoint name while retaining parent context."""
    parts = [part for part in path.split("/") if part]
    selected = parts[-3:] if len(parts) > 2 else parts
    words = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", " ".join(selected))
    return words.replace("_", " ").replace("*", "all").title()


def string_values(values: Collection[Any]) -> tuple[str, ...]:
    """Return endpoint allowed values when all are strings."""
    if not values or not all(isinstance(value, str) for value in values):
        return ()
    return tuple(values)


def is_binary_values(values: Collection[Any]) -> bool:
    """Return whether allowed values represent a boolean state."""
    normalized = frozenset(str(value).casefold() for value in values)
    return normalized in _BINARY_VALUE_SETS


def is_binary_endpoint(endpoint: Any) -> bool:
    """Return whether an endpoint is best represented as a binary sensor/switch."""
    if isinstance(endpoint.value, bool):
        return True
    if is_binary_values(endpoint.allowed_values):
        return True
    return isinstance(endpoint.value, str) and endpoint.value.casefold() in {
        "off",
        "on",
        "false",
        "true",
    }


class BoschEntity(CoordinatorEntity[BoschDataUpdateCoordinator]):
    """Base class for an entity backed by a Bosch endpoint."""

    _attr_has_entity_name = True

    def __init__(
        self,
        runtime_data: BoschRuntimeData,
        path: str,
        *,
        name: str | None = None,
        enabled_default: bool = False,
        diagnostic: bool = False,
    ) -> None:
        """Initialize a Bosch endpoint entity."""
        super().__init__(runtime_data.coordinator, context=path)
        self._runtime_data = runtime_data
        self._path = path
        self._attr_name = name or endpoint_name(path)
        self._attr_unique_id = f"{runtime_data.gateway_uuid}_{path}"
        self._attr_suggested_object_id = f"bosch_compress_{endpoint_object_id(path)}"
        self._attr_entity_registry_enabled_default = enabled_default
        if diagnostic:
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def endpoint(self) -> Any:
        """Return the latest endpoint model."""
        return self.coordinator.endpoint(self._path)

    @property
    def available(self) -> bool:
        """Return whether this specific endpoint has current data."""
        return super().available and self.endpoint is not None

    @property
    def device_info(self) -> DeviceInfo:
        """Return the shared heat-pump device metadata."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._runtime_data.gateway_uuid)},
            manufacturer="Bosch",
            name=DEFAULT_NAME,
            model="Compress heat pump",
            configuration_url=f"http://{self._runtime_data.host}",
        )


class H60Entity(Entity):
    """Base class for one optional H60 supplemental register."""

    _attr_has_entity_name = True

    def __init__(
        self,
        runtime_data: BoschRuntimeData,
        description: H60RegisterDescription,
    ) -> None:
        """Initialize an H60-backed entity."""
        self._runtime_data = runtime_data
        self._description = description
        self._attr_name = description.name
        self._attr_unique_id = f"{runtime_data.gateway_uuid}_h60_{description.register}"
        self._attr_suggested_object_id = f"bosch_compress_{description.key}"
        self._attr_entity_registry_enabled_default = description.enabled_by_default

    @property
    def h60_value(self) -> int | float | None:
        """Return the latest value for this H60 register."""
        if self._runtime_data.h60 is None:
            return None
        return self._runtime_data.h60.value(self._description.register)

    @property
    def available(self) -> bool:
        """Return availability only after the H60 has published this register."""
        return self._runtime_data.h60 is not None and self.h60_value is not None

    @property
    def device_info(self) -> DeviceInfo:
        """Attach supplemental values to the same physical heat-pump device."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._runtime_data.gateway_uuid)},
            manufacturer="Bosch",
            name=DEFAULT_NAME,
            model="Compress heat pump",
            configuration_url=f"http://{self._runtime_data.host}",
        )

    async def async_added_to_hass(self) -> None:
        """Listen for H60 register updates."""
        await super().async_added_to_hass()
        if self._runtime_data.h60 is not None:
            self.async_on_remove(
                self._runtime_data.h60.add_listener(self._handle_h60_update)
            )

    @callback
    def _handle_h60_update(self, register: str, value: int | float) -> None:
        """Write state when this entity's register changes."""
        del value
        if register == self._description.register:
            self.async_write_ha_state()
