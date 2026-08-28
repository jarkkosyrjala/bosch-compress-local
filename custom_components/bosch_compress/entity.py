"""Shared entity helpers for Bosch Compress."""

from __future__ import annotations

import re
from collections.abc import Collection
from typing import Any, Final

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DEFAULT_NAME, DOMAIN
from .coordinator import BoschDataUpdateCoordinator
from .runtime import BoschRuntimeData

_DEFAULT_ENABLED_ENDPOINTS: Final = frozenset(
    {
        "/heatSources/actualModulation",
        "/heatSources/applianceSupplyTemperature",
        "/heatSources/returnTemperature",
        "/notifications",
        "/system/appliance/actualSupplyTemperature",
        "/system/healthStatus",
        "/system/sensors/temperatures/outdoor_t1",
    }
)
_DEFAULT_ENABLED_CIRCUIT_ENDPOINTS: Final = {
    "dhwCircuits": frozenset({"actualTemp", "currentSetpoint", "singleChargeSetpoint"}),
    "heatingCircuits": frozenset(
        {
            "actualSupplyTemperature",
            "currentRoomSetpoint",
            "roomtemperature",
            "supplyTemperatureSetpoint",
        }
    ),
}

_BINARY_VALUE_SETS: Final = (
    frozenset({"off", "on"}),
    frozenset({"false", "true"}),
)


def endpoint_name(path: str) -> str:
    """Build a sentence-case endpoint name while retaining parent context."""
    parts = [part for part in path.split("/") if part]
    selected = parts[-3:] if len(parts) > 2 else parts
    words = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", " ".join(selected))
    label = words.replace("_", " ").replace("*", "all").casefold()
    label = re.sub(r"\bhc(\d+)\b", r"HC\1", label)
    label = re.sub(r"\bdhw(\d+)\b", r"DHW\1", label)
    label = re.sub(r"\bdhw\b", "DHW", label)
    return label[:1].upper() + label[1:]


def is_default_enabled_endpoint(path: str) -> bool:
    """Return whether an endpoint is useful enough to enable by default."""
    if path in _DEFAULT_ENABLED_ENDPOINTS:
        return True
    parts = path.strip("/").split("/")
    return (
        len(parts) == 3
        and parts[1] != ""
        and parts[2] in _DEFAULT_ENABLED_CIRCUIT_ENDPOINTS.get(parts[0], ())
    )


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
