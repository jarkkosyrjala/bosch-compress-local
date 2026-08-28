"""The Bosch Compress integration."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import BoschAuthenticationError, BoschClient, BoschConnectionError
from .const import (
    CONF_ACCESS_TOKEN,
    CONFIG_ENTRY_VERSION,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    PLATFORMS,
)
from .coordinator import BoschDataUpdateCoordinator
from .runtime import BoschConfigEntry, BoschRuntimeData

_LEGACY_H60_TOPIC_PREFIX = "h60_topic_prefix"


def _remove_legacy_h60_registry_entries(
    hass: HomeAssistant, config_entry: BoschConfigEntry
) -> None:
    """Remove obsolete H60 entities owned by this Bosch config entry."""
    registry = er.async_get(hass)
    unique_id_prefix = f"{config_entry.runtime_data.gateway_uuid}_h60_"
    for entry in er.async_entries_for_config_entry(registry, config_entry.entry_id):
        if entry.platform == DOMAIN and entry.unique_id.startswith(unique_id_prefix):
            registry.async_remove(entry.entity_id)


async def async_migrate_entry(
    hass: HomeAssistant, config_entry: ConfigEntry[Any]
) -> bool:
    """Migrate legacy configuration to the current local-only schema."""
    if config_entry.version > CONFIG_ENTRY_VERSION:
        return False
    if config_entry.version == CONFIG_ENTRY_VERSION:
        return True

    data = dict(config_entry.data)
    options = dict(config_entry.options)
    data.pop(_LEGACY_H60_TOPIC_PREFIX, None)
    options.pop(_LEGACY_H60_TOPIC_PREFIX, None)
    hass.config_entries.async_update_entry(
        config_entry,
        data=data,
        options=options,
        version=CONFIG_ENTRY_VERSION,
    )
    return True


async def async_setup_entry(
    hass: HomeAssistant, config_entry: BoschConfigEntry
) -> bool:
    """Set up Bosch Compress from a config entry."""
    host = config_entry.options.get(CONF_HOST, config_entry.data[CONF_HOST])
    client = BoschClient(
        async_get_clientsession(hass),
        host,
        config_entry.data[CONF_ACCESS_TOKEN],
        config_entry.data[CONF_PASSWORD],
    )
    try:
        gateway_uuid = await client.async_gateway_uuid()
    except BoschAuthenticationError as error:
        raise ConfigEntryAuthFailed from error
    except BoschConnectionError as error:
        raise ConfigEntryNotReady from error

    coordinator = BoschDataUpdateCoordinator(
        hass,
        client,
        config_entry=config_entry,
        update_interval=DEFAULT_SCAN_INTERVAL,
    )
    try:
        await coordinator.async_config_entry_first_refresh()
    except BoschAuthenticationError as error:
        raise ConfigEntryAuthFailed from error

    config_entry.runtime_data = BoschRuntimeData(
        coordinator=coordinator,
        gateway_uuid=gateway_uuid,
        host=host,
    )
    config_entry.async_on_unload(config_entry.add_update_listener(_async_reload_entry))
    await hass.config_entries.async_forward_entry_setups(config_entry, PLATFORMS)
    _remove_legacy_h60_registry_entries(hass, config_entry)
    return True


async def async_unload_entry(
    hass: HomeAssistant, config_entry: BoschConfigEntry
) -> bool:
    """Unload a Bosch Compress config entry."""
    return await hass.config_entries.async_unload_platforms(config_entry, PLATFORMS)


async def _async_reload_entry(
    hass: HomeAssistant, config_entry: ConfigEntry[Any]
) -> None:
    """Reload after editable options change."""
    await hass.config_entries.async_reload(config_entry.entry_id)
