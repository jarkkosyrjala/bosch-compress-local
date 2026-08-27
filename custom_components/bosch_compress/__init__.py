"""The Bosch Compress integration."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import BoschAuthenticationError, BoschClient, BoschConnectionError
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_H60_TOPIC_PREFIX,
    DEFAULT_SCAN_INTERVAL,
    PLATFORMS,
)
from .coordinator import BoschDataUpdateCoordinator
from .h60 import H60MqttClient
from .runtime import BoschConfigEntry, BoschRuntimeData


async def async_setup_entry(
    hass: HomeAssistant, config_entry: BoschConfigEntry
) -> bool:
    """Set up Bosch Compress from a config entry."""
    host = config_entry.options.get(CONF_HOST, config_entry.data[CONF_HOST])
    h60_topic_prefix = config_entry.options.get(
        CONF_H60_TOPIC_PREFIX,
        config_entry.data.get(CONF_H60_TOPIC_PREFIX, ""),
    )
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

    h60: H60MqttClient | None = None
    if h60_topic_prefix:
        h60 = H60MqttClient(hass, h60_topic_prefix)
        try:
            await h60.async_start()
        except Exception as error:
            raise ConfigEntryNotReady(
                "H60 MQTT is configured but could not be started"
            ) from error

    config_entry.runtime_data = BoschRuntimeData(
        coordinator=coordinator,
        gateway_uuid=gateway_uuid,
        host=host,
        h60=h60,
    )
    config_entry.async_on_unload(config_entry.add_update_listener(_async_reload_entry))
    await hass.config_entries.async_forward_entry_setups(config_entry, PLATFORMS)
    return True


async def async_unload_entry(
    hass: HomeAssistant, config_entry: BoschConfigEntry
) -> bool:
    """Unload a Bosch Compress config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(config_entry, PLATFORMS)
    if unloaded and config_entry.runtime_data.h60 is not None:
        await config_entry.runtime_data.h60.async_stop()
    return unloaded


async def _async_reload_entry(
    hass: HomeAssistant, config_entry: ConfigEntry[Any]
) -> None:
    """Reload after editable options change."""
    await hass.config_entries.async_reload(config_entry.entry_id)
