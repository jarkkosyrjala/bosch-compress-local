"""Tests for Bosch Compress config-entry lifecycle helpers."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

import custom_components.bosch_compress as integration
from custom_components.bosch_compress import _async_reload_entry as async_reload_entry
from custom_components.bosch_compress import async_unload_entry
from custom_components.bosch_compress.const import CONF_ACCESS_TOKEN, PLATFORMS
from custom_components.bosch_compress.models import BoschEndpoint


def test_unload_entry_delegates_all_forwarded_platforms() -> None:
    """Unloading returns the platform manager's aggregate result."""
    unload = AsyncMock(return_value=True)
    hass = SimpleNamespace(
        config_entries=SimpleNamespace(async_unload_platforms=unload)
    )
    entry = cast(Any, object())

    assert asyncio.run(async_unload_entry(cast(Any, hass), entry)) is True
    unload.assert_awaited_once_with(entry, PLATFORMS)


def test_options_update_listener_reloads_its_entry() -> None:
    """Changing validated options reloads exactly the owning config entry."""
    reload_entry = AsyncMock()
    hass = SimpleNamespace(config_entries=SimpleNamespace(async_reload=reload_entry))
    entry = SimpleNamespace(entry_id="entry-id")

    asyncio.run(async_reload_entry(cast(Any, hass), cast(Any, entry)))

    reload_entry.assert_awaited_once_with("entry-id")


def test_setup_persists_discovered_topology_and_optional_energy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Setup remembers only observed capabilities and preserves future fields."""
    compressor_path = "/recordings/heatSources/total/energyMonitoring/compressor"

    class Client:
        """Return a stable gateway identity."""

        def __init__(self, *_args: Any) -> None:
            """Initialize the fake client."""

        async def async_gateway_uuid(self) -> str:
            """Return the gateway identity."""
            return "gateway-uuid"

    class Coordinator:
        """Expose a newly discovered circuit and energy recording."""

        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            self.discovered_endpoints = {
                "/heatingCircuits/hc3/currentRoomSetpoint": BoschEndpoint(
                    "/heatingCircuits/hc3/currentRoomSetpoint",
                    20.0,
                    "floatValue",
                    False,
                ),
                "/dhwCircuits/dhw2/actualTemp": BoschEndpoint(
                    "/dhwCircuits/dhw2/actualTemp",
                    45.0,
                    "floatValue",
                    False,
                ),
            }
            self.supported_energy_recordings = frozenset({compressor_path})

        async def async_config_entry_first_refresh(self) -> None:
            """Complete the fake first refresh."""

    updates: list[dict[str, Any]] = []

    class ConfigEntries:
        """Capture setup storage and platform forwarding."""

        def async_update_entry(self, _entry: Any, **changes: Any) -> None:
            updates.append(changes)

        async def async_forward_entry_setups(
            self, _entry: Any, _platforms: Any
        ) -> None:
            """Accept platform forwarding."""

    config_entry = SimpleNamespace(
        data={
            "host": "192.0.2.1",
            CONF_ACCESS_TOKEN: "access-token",
            "password": "password",
            "_known_capabilities": {
                "heating_circuits": ["hc1"],
                "future_capability": ["keep"],
            },
        },
        options={},
        add_update_listener=lambda _listener: lambda: None,
        async_on_unload=lambda _unsubscribe: None,
    )
    hass = SimpleNamespace(config_entries=ConfigEntries())
    monkeypatch.setattr(integration, "async_get_clientsession", lambda _hass: object())
    monkeypatch.setattr(integration, "BoschClient", Client)
    monkeypatch.setattr(integration, "BoschDataUpdateCoordinator", Coordinator)

    assert asyncio.run(
        integration.async_setup_entry(cast(Any, hass), cast(Any, config_entry))
    )

    assert len(updates) == 1
    assert updates[0]["data"]["_known_capabilities"] == {
        "heating_circuits": ["hc1", "hc3"],
        "dhw_circuits": ["dhw2"],
        "dhw_charge_circuits": [],
        "dhw_program_circuits": [],
        "energy_recordings": [compressor_path],
        "future_capability": ["keep"],
    }
    assert config_entry.runtime_data.topology.heating_circuits == frozenset(
        {"hc1", "hc3"}
    )
