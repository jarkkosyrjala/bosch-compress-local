"""Tests for Bosch Compress config-entry schema migration."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast

import pytest
import voluptuous as vol
from homeassistant.const import CONF_HOST, CONF_PASSWORD

import custom_components.bosch_compress as bosch_integration
from custom_components.bosch_compress import async_migrate_entry
from custom_components.bosch_compress.config_flow import (
    BoschCompressConfigFlow,
    _user_schema,
)
from custom_components.bosch_compress.const import (
    CONF_ACCESS_TOKEN,
    CONFIG_ENTRY_VERSION,
    DOMAIN,
)

_LEGACY_H60_TOPIC_PREFIX = "h60_topic_prefix"


class _ConfigEntries:
    """Record config-entry updates without starting Home Assistant."""

    def __init__(self) -> None:
        """Initialize an empty update record."""
        self.updates: list[dict[str, Any]] = []

    def async_update_entry(self, _entry: Any, **changes: Any) -> None:
        """Record one migration update."""
        self.updates.append(changes)


class _EntityRegistry:
    """Record entity removals without starting Home Assistant."""

    def __init__(self) -> None:
        """Initialize an empty removal record."""
        self.removed: list[str] = []

    def async_remove(self, entity_id: str) -> None:
        """Record one registry removal."""
        self.removed.append(entity_id)


def test_legacy_h60_registry_cleanup_is_exactly_scoped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only this config entry's Bosch-owned exact H60 identities are removed."""
    target_entry_id = "target-config-entry"
    entries = [
        SimpleNamespace(
            entity_id="sensor.legacy_one",
            unique_id="gateway-uuid_h60_0001",
            platform=DOMAIN,
            config_entry_id=target_entry_id,
        ),
        SimpleNamespace(
            entity_id="switch.legacy_two",
            unique_id="gateway-uuid_h60_1231",
            platform=DOMAIN,
            config_entry_id=target_entry_id,
        ),
        SimpleNamespace(
            entity_id="sensor.similar_prefix",
            unique_id="gateway-uuid_h60x_0002",
            platform=DOMAIN,
            config_entry_id=target_entry_id,
        ),
        SimpleNamespace(
            entity_id="sensor.other_gateway",
            unique_id="other-gateway_h60_0003",
            platform=DOMAIN,
            config_entry_id=target_entry_id,
        ),
        SimpleNamespace(
            entity_id="sensor.native",
            unique_id="gateway-uuid_native_sensor",
            platform=DOMAIN,
            config_entry_id=target_entry_id,
        ),
        SimpleNamespace(
            entity_id="sensor.mqtt_same_entry",
            unique_id="gateway-uuid_h60_0004",
            platform="mqtt",
            config_entry_id=target_entry_id,
        ),
        SimpleNamespace(
            entity_id="sensor.other_bosch_entry",
            unique_id="gateway-uuid_h60_0005",
            platform=DOMAIN,
            config_entry_id="other-config-entry",
        ),
        SimpleNamespace(
            entity_id="sensor.other_mqtt_entry",
            unique_id="gateway-uuid_h60_0006",
            platform="mqtt",
            config_entry_id="other-config-entry",
        ),
    ]
    registry = _EntityRegistry()
    entry_queries: list[str] = []

    def entries_for_config_entry(
        candidate_registry: _EntityRegistry, config_entry_id: str
    ) -> list[Any]:
        assert candidate_registry is registry
        entry_queries.append(config_entry_id)
        return [entry for entry in entries if entry.config_entry_id == config_entry_id]

    monkeypatch.setattr(bosch_integration.er, "async_get", lambda _hass: registry)
    monkeypatch.setattr(
        bosch_integration.er,
        "async_entries_for_config_entry",
        entries_for_config_entry,
    )
    config_entry = SimpleNamespace(
        entry_id=target_entry_id,
        runtime_data=SimpleNamespace(gateway_uuid="gateway-uuid"),
    )

    bosch_integration._remove_legacy_h60_registry_entries(
        cast(Any, object()), cast(Any, config_entry)
    )

    assert entry_queries == [target_entry_id]
    assert registry.removed == ["sensor.legacy_one", "switch.legacy_two"]


def test_setup_cleans_legacy_registry_after_platform_setup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Platform-specific ID migrations finish before leftover cleanup runs."""
    events: list[str] = []

    class Client:
        """Return a stable gateway identity without network access."""

        def __init__(self, *_args: Any) -> None:
            """Initialize the fake client."""

        async def async_gateway_uuid(self) -> str:
            """Return the gateway identity used by cleanup."""
            return "gateway-uuid"

    class Coordinator:
        """Complete the initial refresh without network access."""

        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            """Initialize the fake coordinator."""

        async def async_config_entry_first_refresh(self) -> None:
            """Complete the fake refresh."""

    class ConfigEntries:
        """Record the full platform-forwarding lifecycle."""

        async def async_forward_entry_setups(
            self, _entry: Any, _platforms: Any
        ) -> None:
            """Finish every platform before returning to integration setup."""
            events.append("platforms_started")
            await asyncio.sleep(0)
            events.append("platforms_finished")

    config_entry = SimpleNamespace(
        entry_id="target-config-entry",
        data={
            CONF_HOST: "192.0.2.1",
            CONF_ACCESS_TOKEN: "login",
            CONF_PASSWORD: "password",
        },
        options={},
        add_update_listener=lambda _listener: lambda: None,
        async_on_unload=lambda _unsubscribe: None,
    )
    hass = SimpleNamespace(config_entries=ConfigEntries())
    monkeypatch.setattr(
        bosch_integration, "async_get_clientsession", lambda _hass: object()
    )
    monkeypatch.setattr(bosch_integration, "BoschClient", Client)
    monkeypatch.setattr(bosch_integration, "BoschDataUpdateCoordinator", Coordinator)
    monkeypatch.setattr(
        bosch_integration,
        "_remove_legacy_h60_registry_entries",
        lambda _hass, _entry: events.append("cleanup"),
    )

    assert asyncio.run(
        bosch_integration.async_setup_entry(cast(Any, hass), cast(Any, config_entry))
    )
    assert events == ["platforms_started", "platforms_finished", "cleanup"]


def test_migration_removes_legacy_h60_configuration() -> None:
    """Version-one entries lose obsolete transport data and options."""
    config_entries = _ConfigEntries()
    hass = SimpleNamespace(config_entries=config_entries)
    entry = SimpleNamespace(
        version=1,
        data={
            CONF_HOST: "192.0.2.1",
            CONF_ACCESS_TOKEN: "login",
            CONF_PASSWORD: "password",
            _LEGACY_H60_TOPIC_PREFIX: "legacy-prefix",
        },
        options={
            CONF_HOST: "192.0.2.2",
            _LEGACY_H60_TOPIC_PREFIX: "legacy-option-prefix",
        },
    )

    assert asyncio.run(async_migrate_entry(cast(Any, hass), cast(Any, entry)))
    assert config_entries.updates == [
        {
            "data": {
                CONF_HOST: "192.0.2.1",
                CONF_ACCESS_TOKEN: "login",
                CONF_PASSWORD: "password",
            },
            "options": {CONF_HOST: "192.0.2.2"},
            "version": CONFIG_ENTRY_VERSION,
        }
    ]


def test_current_entry_needs_no_migration() -> None:
    """Current entries are accepted without rewriting storage."""
    config_entries = _ConfigEntries()
    hass = SimpleNamespace(config_entries=config_entries)
    entry = SimpleNamespace(version=CONFIG_ENTRY_VERSION, data={}, options={})

    assert asyncio.run(async_migrate_entry(cast(Any, hass), cast(Any, entry)))
    assert config_entries.updates == []


def test_future_entry_version_is_rejected() -> None:
    """An entry from a newer schema must not be downgraded."""
    config_entries = _ConfigEntries()
    hass = SimpleNamespace(config_entries=config_entries)
    entry = SimpleNamespace(version=CONFIG_ENTRY_VERSION + 1, data={}, options={})

    assert not asyncio.run(async_migrate_entry(cast(Any, hass), cast(Any, entry)))
    assert config_entries.updates == []


def test_user_schema_is_local_only() -> None:
    """Initial setup accepts only the Bosch local connection fields."""
    schema = _user_schema()
    values = {
        CONF_HOST: "192.0.2.1",
        CONF_ACCESS_TOKEN: "login",
        CONF_PASSWORD: "password",
    }

    assert schema(values) == values
    with pytest.raises(vol.MultipleInvalid):
        schema({**values, _LEGACY_H60_TOPIC_PREFIX: "legacy-prefix"})
    assert BoschCompressConfigFlow.VERSION == CONFIG_ENTRY_VERSION
