"""Tests for the Bosch data update coordinator."""

from __future__ import annotations

import asyncio
from datetime import date, datetime
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import UpdateFailed

import custom_components.bosch_compress.coordinator as coordinator_module
from custom_components.bosch_compress.api import (
    BoschConnectionError,
    BoschForbiddenError,
)
from custom_components.bosch_compress.coordinator import (
    ENERGY_RECORDING_PATHS,
    BoschDataUpdateCoordinator,
)
from custom_components.bosch_compress.models import BoschEndpoint


def endpoint(path: str, value: object) -> BoschEndpoint:
    """Build a simple test endpoint."""
    return BoschEndpoint(path, value, "floatValue", False)


class FakeClient:
    """Fake typed Bosch client for coordinator tests."""

    def __init__(self) -> None:
        """Initialize fake endpoint data."""
        self.discovery = {
            "/gateway/uuid": BoschEndpoint(
                "/gateway/uuid", "uuid", "stringValue", False
            ),
            "/optional": endpoint("/optional", 1),
            "/failing": endpoint("/failing", 2),
        }
        self.responses: dict[str, BoschEndpoint | Exception] = {
            "/gateway/uuid": self.discovery["/gateway/uuid"],
            "/optional": endpoint("/optional", 3),
            "/failing": BoschForbiddenError("forbidden"),
        }
        self.writes: list[tuple[str, object]] = []
        self.energy: dict[str, float | Exception] = {
            path: 2.5 if path.endswith("/compressor") else 0.5
            for path in ENERGY_RECORDING_PATHS
        }
        self.energy_requests: list[tuple[str, date]] = []

    async def async_discover_endpoints(self) -> dict[str, BoschEndpoint]:
        """Return fake discovery data."""
        return dict(self.discovery)

    async def async_get_endpoint(self, path: str) -> BoschEndpoint:
        """Return or raise a fake endpoint response."""
        response = self.responses[path]
        if isinstance(response, Exception):
            raise response
        return response

    async def async_put(self, path: str, value: object) -> None:
        """Record a fake endpoint write."""
        self.writes.append((path, value))

    async def async_get_daily_energy(self, path: str, day: date) -> float:
        """Return a fake current-day energy total."""
        self.energy_requests.append((path, day))
        response = self.energy[path]
        if isinstance(response, Exception):
            raise response
        return response


def test_coordinator_discovers_and_polls_only_registered_paths() -> None:
    """The coordinator polls its core path and explicitly active paths."""

    async def run_test() -> None:
        hass = HomeAssistant("/tmp")
        client = FakeClient()
        coordinator = BoschDataUpdateCoordinator(
            hass,
            client,  # type: ignore[arg-type]
            config_entry=None,
        )
        await coordinator._async_setup()
        initial_energy_requests = list(client.energy_requests)
        remove_listener = coordinator.async_add_listener(
            lambda: None, context="/optional"
        )

        data = await coordinator._async_update_data()

        assert data["/optional"].value == 3
        assert (
            data["/recordings/heatSources/total/energyMonitoring/compressor"].value
            == 2.5
        )
        assert (
            data["/recordings/heatSources/total/energyMonitoring/eheater"].value == 0.5
        )
        assert {path for path, _day in client.energy_requests} == ENERGY_RECORDING_PATHS
        assert client.energy_requests == initial_energy_requests
        assert "/failing" not in coordinator.endpoint_errors
        remove_listener()
        unregister = coordinator.register_path("/optional")
        assert coordinator._poll_paths() == {
            "/gateway/uuid",
            "/optional",
        }
        unregister()
        assert coordinator._poll_paths() == {"/gateway/uuid"}

    asyncio.run(run_test())


def test_invalid_measurement_is_not_a_transport_failure() -> None:
    """Discovery and polling keep missing measurements and recover in place."""

    async def run_test() -> None:
        hass = HomeAssistant("/tmp")
        client = FakeClient()
        path = "/optional"
        missing = BoschEndpoint(path, -3276.8, "floatValue", True, unit="C")
        client.discovery[path] = missing
        client.responses[path] = missing
        coordinator = BoschDataUpdateCoordinator(hass, client, config_entry=None)
        await coordinator._async_setup()
        assert coordinator.discovered_endpoints[path].value is None
        unregister = coordinator.register_path(path)
        try:
            for reading in (-3276.8, 20.5, -3276.8, 21.0):
                client.responses[path] = BoschEndpoint(
                    path, reading, "floatValue", True, unit="C"
                )
                data = await coordinator._async_update_data()
                assert data[path].writable
                assert data[path].value == (None if reading == -3276.8 else reading)
                assert path not in coordinator.endpoint_errors
                assert "/gateway/uuid" in data
        finally:
            unregister()

    asyncio.run(run_test())


def test_energy_recordings_poll_only_with_an_enabled_entity_context() -> None:
    """Supported recordings stop polling when their entity is disabled."""

    async def run_test() -> None:
        hass = HomeAssistant("/tmp")
        client = FakeClient()
        coordinator = BoschDataUpdateCoordinator(
            hass,
            client,  # type: ignore[arg-type]
            config_entry=None,
        )
        await coordinator._async_setup()
        setup_request_count = len(client.energy_requests)
        path = "/recordings/heatSources/total/energyMonitoring/compressor"

        await coordinator._async_update_data()
        assert len(client.energy_requests) == setup_request_count

        remove_listener = coordinator.async_add_listener(lambda: None, context=path)
        await coordinator._async_update_data()
        assert len(client.energy_requests) == setup_request_count + 1
        assert client.energy_requests[-1][0] == path

        remove_listener()
        await coordinator._async_update_data()
        assert len(client.energy_requests) == setup_request_count + 1

    asyncio.run(run_test())


def test_unsupported_energy_recordings_do_not_create_entities() -> None:
    """A gateway without recordings does not get unavailable energy sensors."""

    async def run_test() -> None:
        hass = HomeAssistant("/tmp")
        client = FakeClient()
        for path in ENERGY_RECORDING_PATHS:
            client.energy[path] = BoschForbiddenError("unsupported")
        coordinator = BoschDataUpdateCoordinator(
            hass,
            client,  # type: ignore[arg-type]
            config_entry=None,
        )

        await coordinator._async_setup()

        assert not ENERGY_RECORDING_PATHS & coordinator.discovered_endpoints.keys()
        assert coordinator.supported_energy_recordings == frozenset()

    asyncio.run(run_test())


def test_known_energy_recording_survives_a_temporary_setup_failure() -> None:
    """A previously observed recording remains available for later recovery."""

    async def run_test() -> None:
        hass = HomeAssistant("/tmp")
        client = FakeClient()
        path = "/recordings/heatSources/total/energyMonitoring/compressor"
        client.energy[path] = BoschConnectionError("temporarily offline")
        config_entry = SimpleNamespace(
            data={"_known_capabilities": {"energy_recordings": [path]}},
            async_on_unload=lambda _callback: None,
        )
        coordinator = BoschDataUpdateCoordinator(
            hass,
            client,  # type: ignore[arg-type]
            config_entry=cast(Any, config_entry),
        )

        await coordinator._async_setup()

        assert coordinator.discovered_endpoints[path].value is None
        assert coordinator.supported_energy_recordings == frozenset(
            ENERGY_RECORDING_PATHS
        )

    asyncio.run(run_test())


def test_energy_recording_query_rolls_over_with_home_assistant_local_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The enabled energy sensor switches dates at HA's local midnight."""
    before_midnight = datetime(2026, 8, 28, 23, 59)
    after_midnight = datetime(2026, 8, 29, 0, 0)
    times = iter((before_midnight, after_midnight))
    monkeypatch.setattr(coordinator_module.dt_util, "now", lambda: next(times))

    async def run_test() -> None:
        hass = HomeAssistant("/tmp")
        client = FakeClient()
        coordinator = BoschDataUpdateCoordinator(
            hass,
            client,  # type: ignore[arg-type]
            config_entry=None,
        )
        await coordinator._async_setup()
        remove_listeners = [
            coordinator.async_add_listener(lambda: None, context=path)
            for path in ENERGY_RECORDING_PATHS
        ]

        await coordinator._async_update_data()

        assert {
            path for path, _day in client.energy_requests[-2:]
        } == ENERGY_RECORDING_PATHS
        assert {day for _path, day in client.energy_requests[-2:]} == {
            date(2026, 8, 29)
        }
        assert {day for _path, day in client.energy_requests[:-2]} == {
            date(2026, 8, 28)
        }
        for remove_listener in remove_listeners:
            remove_listener()

    asyncio.run(run_test())


def test_coordinator_removes_failed_path_without_taking_successful_data_down() -> None:
    """A partial failure removes stale data so entity availability can update."""

    async def run_test() -> None:
        hass = HomeAssistant("/tmp")
        client = FakeClient()
        coordinator = BoschDataUpdateCoordinator(
            hass,
            client,  # type: ignore[arg-type]
            config_entry=None,
        )
        await coordinator._async_setup()
        coordinator.register_path("/optional")
        coordinator.register_path("/failing")

        data = await coordinator._async_update_data()

        assert data["/optional"].value == 3
        assert "/failing" not in data
        assert isinstance(coordinator.endpoint_errors["/failing"], BoschForbiddenError)
        assert coordinator.endpoint("/failing") is None

    asyncio.run(run_test())


def test_coordinator_notifies_on_partial_failure_and_same_value_recovery() -> None:
    """Removing and readding a path changes data even when its value is unchanged."""

    async def run_test() -> None:
        hass = HomeAssistant("/tmp")
        client = FakeClient()
        client.responses["/optional"] = client.discovery["/optional"]
        coordinator = BoschDataUpdateCoordinator(
            hass,
            client,  # type: ignore[arg-type]
            config_entry=None,
        )
        await coordinator._async_setup()
        coordinator.data = dict(coordinator.discovered_endpoints)
        coordinator.register_path("/failing")
        updates = 0

        def handle_update() -> None:
            nonlocal updates
            updates += 1

        remove_listener = coordinator.async_add_listener(handle_update)

        await coordinator.async_refresh()

        assert updates == 1
        assert coordinator.data is not None
        assert "/failing" not in coordinator.data
        assert coordinator.endpoint("/failing") is None

        client.responses["/failing"] = client.discovery["/failing"]
        await coordinator.async_refresh()

        assert updates == 2
        assert coordinator.endpoint("/failing") == client.discovery["/failing"]
        remove_listener()

    asyncio.run(run_test())


def test_coordinator_fails_when_gateway_is_unreachable() -> None:
    """A total connection failure marks the coordinator update failed."""

    async def run_test() -> None:
        hass = HomeAssistant("/tmp")
        client = FakeClient()
        client.responses["/gateway/uuid"] = BoschConnectionError("offline")
        for path in ENERGY_RECORDING_PATHS:
            client.energy[path] = BoschConnectionError("offline")
        coordinator = BoschDataUpdateCoordinator(
            hass,
            client,  # type: ignore[arg-type]
            config_entry=None,
        )
        await coordinator._async_setup()

        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()

    asyncio.run(run_test())


def test_coordinator_write_refreshes() -> None:
    """A successful endpoint write requests a coordinated refresh."""

    async def run_test() -> None:
        hass = HomeAssistant("/tmp")
        client = FakeClient()
        coordinator = BoschDataUpdateCoordinator(
            hass,
            client,  # type: ignore[arg-type]
            config_entry=None,
        )
        coordinator.async_request_refresh = AsyncMock()

        await coordinator.async_write("/optional", 5)

        assert client.writes == [("/optional", 5)]
        coordinator.async_request_refresh.assert_awaited_once()

    asyncio.run(run_test())
