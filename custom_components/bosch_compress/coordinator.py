"""Data update coordinator for the Bosch Compress integration."""

from __future__ import annotations

import logging
from collections.abc import Callable, Collection
from datetime import timedelta
from typing import Any, Final

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    BoschApiError,
    BoschAuthenticationError,
    BoschClient,
    BoschConnectionError,
)
from .models import BoschEndpoint

_LOGGER = logging.getLogger(__name__)

CORE_POLL_PATHS: Final = frozenset({"/gateway/uuid"})
DEFAULT_UPDATE_INTERVAL: Final = timedelta(seconds=60)


class BoschDataUpdateCoordinator(DataUpdateCoordinator[dict[str, BoschEndpoint]]):
    """Coordinate discovery, polling, and writes for a Bosch gateway."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: BoschClient,
        *,
        config_entry: ConfigEntry | None = None,
        update_interval: timedelta = DEFAULT_UPDATE_INTERVAL,
        core_paths: Collection[str] = CORE_POLL_PATHS,
    ) -> None:
        """Initialize the Bosch coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name="Bosch Compress",
            update_interval=update_interval,
            always_update=False,
        )
        self.client = client
        self.discovered_endpoints: dict[str, BoschEndpoint] = {}
        self.endpoint_errors: dict[str, BoschApiError] = {}
        self._core_paths = frozenset(core_paths)
        self._registered_paths: dict[str, int] = {}

    async def _async_setup(self) -> None:
        """Discover the gateway endpoint tree once before the first refresh."""
        self.discovered_endpoints = await self.client.async_discover_endpoints()

    @callback
    def register_path(self, path: str) -> Callable[[], None]:
        """Register an endpoint for polling while an entity is enabled."""
        self._registered_paths[path] = self._registered_paths.get(path, 0) + 1

        @callback
        def unregister() -> None:
            registrations = self._registered_paths.get(path, 0)
            if registrations <= 1:
                self._registered_paths.pop(path, None)
            else:
                self._registered_paths[path] = registrations - 1

        return unregister

    @callback
    def endpoint(self, path: str) -> BoschEndpoint | None:
        """Return the latest endpoint, falling back to discovery metadata."""
        if path in self.endpoint_errors:
            return None
        if self.data is not None and (endpoint := self.data.get(path)) is not None:
            return endpoint
        return self.discovered_endpoints.get(path)

    @callback
    def _poll_paths(self) -> set[str]:
        """Return core paths plus paths used by enabled coordinator entities."""
        context_paths = {
            context
            for context in self.async_contexts()
            if isinstance(context, str) and context.startswith("/")
        }
        return set(self._core_paths) | self._registered_paths.keys() | context_paths

    async def _async_update_data(self) -> dict[str, BoschEndpoint]:
        """Poll active endpoints while preserving successful partial data."""
        data = dict(self.data or self.discovered_endpoints)
        errors: dict[str, BoschApiError] = {}
        successful_requests = 0

        for path in sorted(self._poll_paths()):
            try:
                data[path] = await self.client.async_get_endpoint(path)
                successful_requests += 1
            except BoschApiError as error:
                errors[path] = error
                _LOGGER.debug("Unable to update Bosch endpoint %s: %s", path, error)

        self.endpoint_errors = errors
        if errors and not successful_requests:
            first_error = next(iter(errors.values()))
            if isinstance(first_error, BoschAuthenticationError):
                raise ConfigEntryAuthFailed from first_error
            if isinstance(first_error, BoschConnectionError):
                raise UpdateFailed(
                    "Unable to update the Bosch gateway"
                ) from first_error
            if len(errors) == len(self._poll_paths()):
                raise UpdateFailed(
                    "No Bosch gateway endpoints could be updated"
                ) from first_error

        return data

    async def async_write(self, path: str, value: Any) -> None:
        """Write an endpoint value and request an immediate coordinated refresh."""
        await self.client.async_put(path, value)
        await self.async_request_refresh()
