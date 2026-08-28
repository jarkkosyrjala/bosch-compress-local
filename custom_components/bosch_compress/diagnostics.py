"""Diagnostics support for Bosch Compress."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .runtime import BoschConfigEntry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, config_entry: BoschConfigEntry
) -> dict[str, Any]:
    """Return useful metadata without credentials, addresses, or live values."""
    runtime_data = config_entry.runtime_data
    coordinator = runtime_data.coordinator
    endpoints = coordinator.discovered_endpoints
    entity_registry = er.async_get(hass)
    registry_entries = er.async_entries_for_config_entry(
        entity_registry, config_entry.entry_id
    )

    return {
        "entry": {
            "version": config_entry.version,
            "state": config_entry.state,
        },
        "discovery": {
            "endpoint_count": len(endpoints),
            "writable_endpoint_count": sum(
                endpoint.writable for endpoint in endpoints.values()
            ),
            "endpoint_types": sorted(
                {endpoint.value_type for endpoint in endpoints.values()}
            ),
            "endpoint_paths": sorted(endpoints),
        },
        "polling": {
            "last_update_success": coordinator.last_update_success,
            "endpoint_errors": {
                path: type(error).__name__
                for path, error in sorted(coordinator.endpoint_errors.items())
            },
        },
        "entities": {
            "count": len(registry_entries),
            "disabled_count": sum(
                entry.disabled_by is not None for entry in registry_entries
            ),
        },
    }
