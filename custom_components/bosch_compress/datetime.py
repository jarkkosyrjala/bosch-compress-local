"""Date-time entities for Bosch Compress."""

from __future__ import annotations

from datetime import datetime, tzinfo
from typing import override

from homeassistant.components.datetime import DateTimeEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .entity import BoschEntity
from .runtime import BoschConfigEntry, BoschRuntimeData

PARALLEL_UPDATES = 1

_GATEWAY_DATETIME = "/gateway/DateTime"


class BoschGatewayDateTime(BoschEntity, DateTimeEntity):
    """Represent the Bosch gateway's local clock."""

    def __init__(self, runtime_data: BoschRuntimeData, timezone: tzinfo) -> None:
        """Initialize the gateway clock entity."""
        super().__init__(runtime_data, _GATEWAY_DATETIME, name="Gateway Date Time")
        self._attr_entity_category = EntityCategory.CONFIG
        self._timezone = timezone

    @property
    @override
    def native_value(self) -> datetime | None:
        """Return the gateway local time as an aware datetime."""
        value = self.endpoint.value
        if not isinstance(value, str):
            return None
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=self._timezone)
        return parsed.astimezone(self._timezone)

    @override
    async def async_set_value(self, value: datetime) -> None:
        """Write the gateway clock in its local time format."""
        local_value = value.astimezone(self._timezone).replace(tzinfo=None)
        await self.coordinator.async_write(
            self._path, local_value.isoformat(timespec="seconds")
        )


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: BoschConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Bosch gateway date-time entity when supported."""
    runtime_data = config_entry.runtime_data
    endpoint = runtime_data.coordinator.discovered_endpoints.get(_GATEWAY_DATETIME)
    timezone = dt_util.get_time_zone(hass.config.time_zone)
    if endpoint is not None and endpoint.writable and timezone is not None:
        async_add_entities([BoschGatewayDateTime(runtime_data, timezone)])
