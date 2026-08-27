"""Binary sensor entities for Bosch Compress."""

from __future__ import annotations

from typing import override

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import BoschEntity, H60Entity, is_binary_endpoint
from .h60 import H60_REGISTERS, H60RegisterDescription, H60RegisterKind
from .runtime import BoschConfigEntry, BoschRuntimeData

PARALLEL_UPDATES = 0


class BoschEndpointBinarySensor(BoschEntity, BinarySensorEntity):
    """Represent a read-only Bosch boolean endpoint."""

    def __init__(self, runtime_data: BoschRuntimeData, path: str) -> None:
        """Initialize a binary sensor."""
        super().__init__(runtime_data, path, diagnostic=True)

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


class BoschCompressorRunningBinarySensor(BoschEntity, BinarySensorEntity):
    """Represent compressor state derived from native Bosch modulation."""

    _attr_device_class = BinarySensorDeviceClass.RUNNING

    def __init__(self, runtime_data: BoschRuntimeData) -> None:
        """Initialize the derived compressor state."""
        super().__init__(
            runtime_data,
            "/heatSources/actualModulation",
            name="Compressor Running",
            enabled_default=True,
        )
        self._attr_unique_id = f"{runtime_data.gateway_uuid}_compressor_running"
        self._attr_suggested_object_id = "bosch_compress_compressor_running"

    @property
    @override
    def is_on(self) -> bool | None:
        """Return whether the compressor reports non-zero modulation."""
        value = self.endpoint.value
        if isinstance(value, int | float) and not isinstance(value, bool):
            return value > 0
        return None


class H60SupplementalBinarySensor(H60Entity, BinarySensorEntity):
    """Represent an H60-only binary state."""

    def __init__(
        self,
        runtime_data: BoschRuntimeData,
        description: H60RegisterDescription,
    ) -> None:
        """Initialize an H60 binary sensor."""
        super().__init__(runtime_data, description)
        if description.register == "1A20":
            self._attr_device_class = BinarySensorDeviceClass.PROBLEM
        elif description.register == "1A0C":
            self._attr_device_class = BinarySensorDeviceClass.RUNNING

    @property
    @override
    def is_on(self) -> bool | None:
        """Return the latest H60 binary state."""
        value = self.h60_value
        return value != 0 if value is not None else None


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: BoschConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up read-only Bosch boolean endpoints."""
    del hass
    runtime_data = config_entry.runtime_data
    entities: list[BinarySensorEntity] = [
        BoschEndpointBinarySensor(runtime_data, path)
        for path, endpoint in runtime_data.coordinator.discovered_endpoints.items()
        if not endpoint.writable
        and not endpoint.is_reference
        and is_binary_endpoint(endpoint)
    ]
    if "/heatSources/actualModulation" in runtime_data.coordinator.discovered_endpoints:
        entities.append(BoschCompressorRunningBinarySensor(runtime_data))
    if runtime_data.h60 is not None:
        entities.extend(
            H60SupplementalBinarySensor(runtime_data, description)
            for description in H60_REGISTERS.values()
            if description.kind is H60RegisterKind.BINARY_SENSOR
            and description.register != "1A01"
        )
    async_add_entities(entities)
