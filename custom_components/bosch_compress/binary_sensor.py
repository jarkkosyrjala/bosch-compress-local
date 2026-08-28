"""Binary sensor entities for Bosch Compress."""

from __future__ import annotations

from typing import override

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import BoschEntity, is_binary_endpoint
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
    async_add_entities(entities)
