"""Tests for the Bosch data update coordinator."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.bosch_compress.api import (
    BoschConnectionError,
    BoschForbiddenError,
)
from custom_components.bosch_compress.coordinator import BoschDataUpdateCoordinator
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
        remove_listener = coordinator.async_add_listener(
            lambda: None, context="/optional"
        )

        data = await coordinator._async_update_data()

        assert data["/optional"].value == 3
        assert "/failing" not in coordinator.endpoint_errors
        remove_listener()
        unregister = coordinator.register_path("/optional")
        assert coordinator._poll_paths() == {"/gateway/uuid", "/optional"}
        unregister()
        assert coordinator._poll_paths() == {"/gateway/uuid"}

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
