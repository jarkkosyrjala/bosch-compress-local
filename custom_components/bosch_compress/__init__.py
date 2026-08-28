"""The Bosch Compress integration."""

from __future__ import annotations

from homeassistant.const import CONF_HOST, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import BoschAuthenticationError, BoschClient, BoschConnectionError
from .const import (
    CONF_ACCESS_TOKEN,
    DEFAULT_SCAN_INTERVAL,
    PLATFORMS,
)
from .coordinator import BoschDataUpdateCoordinator
from .runtime import BoschConfigEntry, BoschRuntimeData
from .topology import (
    CONF_KNOWN_CAPABILITIES,
    BoschTopology,
    discover_topology,
    merge_capability_storage,
)


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

    stored_capabilities = config_entry.data.get(CONF_KNOWN_CAPABILITIES)
    topology = BoschTopology.from_mapping(stored_capabilities).merged(
        discover_topology(getattr(coordinator, "discovered_endpoints", {}))
    )
    merged_capabilities = merge_capability_storage(stored_capabilities, topology)
    merged_capabilities["energy_recordings"] = sorted(
        getattr(coordinator, "supported_energy_recordings", ())
    )
    if (
        stored_capabilities is not None
        or topology != BoschTopology()
        or merged_capabilities["energy_recordings"]
    ) and stored_capabilities != merged_capabilities:
        hass.config_entries.async_update_entry(
            config_entry,
            data={
                **config_entry.data,
                CONF_KNOWN_CAPABILITIES: merged_capabilities,
            },
        )

    config_entry.runtime_data = BoschRuntimeData(
        coordinator=coordinator,
        gateway_uuid=gateway_uuid,
        host=host,
        topology=topology,
    )
    config_entry.async_on_unload(config_entry.add_update_listener(_async_reload_entry))
    await hass.config_entries.async_forward_entry_setups(config_entry, PLATFORMS)
    return True


async def async_unload_entry(
    hass: HomeAssistant, config_entry: BoschConfigEntry
) -> bool:
    """Unload a Bosch Compress config entry."""
    return await hass.config_entries.async_unload_platforms(config_entry, PLATFORMS)


async def _async_reload_entry(
    hass: HomeAssistant, config_entry: BoschConfigEntry
) -> None:
    """Reload after editable options change."""
    await hass.config_entries.async_reload(config_entry.entry_id)
