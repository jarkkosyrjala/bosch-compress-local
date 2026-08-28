"""Tests for Bosch-native domestic-hot-water controls."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast

import pytest

import custom_components.bosch_compress.select as select_platform
import custom_components.bosch_compress.switch as switch_platform
from custom_components.bosch_compress.const import DOMAIN
from custom_components.bosch_compress.h60 import H60_REGISTERS
from custom_components.bosch_compress.models import BoschEndpoint
from custom_components.bosch_compress.runtime import BoschRuntimeData
from custom_components.bosch_compress.select import (
    BoschDhwProgramSelect,
    H60SupplementalSelect,
)
from custom_components.bosch_compress.select import (
    async_setup_entry as async_setup_selects,
)
from custom_components.bosch_compress.switch import (
    BoschExtraWarmWaterSwitch,
    H60SupplementalSwitch,
)
from custom_components.bosch_compress.switch import (
    async_setup_entry as async_setup_switches,
)

_CHARGE_PATH = "/dhwCircuits/dhw1/charge"
_PROGRAM_PATH = "/dhwCircuits/dhw1/operationMode"


class _FakeCoordinator:
    """Minimal coordinator for native DHW entity tests."""

    def __init__(self, endpoints: list[BoschEndpoint]) -> None:
        self.last_update_success = True
        self.discovered_endpoints = {endpoint.path: endpoint for endpoint in endpoints}
        self.writes: list[tuple[str, Any]] = []

    def endpoint(self, path: str) -> BoschEndpoint | None:
        """Return one endpoint snapshot."""
        return self.discovered_endpoints.get(path)

    async def async_write(self, path: str, value: Any) -> None:
        """Record one native gateway write."""
        self.writes.append((path, value))

    def async_add_listener(self, update_callback: Any, context: Any = None) -> Any:
        """Return a no-op coordinator unsubscribe callback."""
        del update_callback, context
        return lambda: None


class _FakeH60:
    """Minimal H60 object used to check native transport precedence."""

    def value(self, register: str) -> None:
        """Return no supplemental state."""
        del register
        return None


class _FakeEntityRegistry:
    """Small registry surface for verifying unique-id migrations."""

    def __init__(self, entries: dict[tuple[str, str, str], str]) -> None:
        self.entries = dict(entries)
        self.updates: list[tuple[str, str]] = []

    def async_get_entity_id(
        self, domain: str, platform: str, unique_id: str
    ) -> str | None:
        """Resolve one entity by registry identity."""
        return self.entries.get((domain, platform, unique_id))

    def async_update_entity(self, entity_id: str, *, new_unique_id: str) -> None:
        """Record and apply one unique-id migration."""
        old_key = next(
            key for key, candidate in self.entries.items() if candidate == entity_id
        )
        del self.entries[old_key]
        self.entries[(old_key[0], old_key[1], new_unique_id)] = entity_id
        self.updates.append((entity_id, new_unique_id))


def _endpoint(
    path: str,
    value: str,
    allowed_values: tuple[str, ...],
    *,
    writable: bool = True,
) -> BoschEndpoint:
    """Build one Bosch string endpoint."""
    return BoschEndpoint(
        path=path,
        value=value,
        value_type="stringValue",
        writable=writable,
        allowed_values=allowed_values,
    )


def _runtime(
    endpoints: list[BoschEndpoint], *, h60: _FakeH60 | None = None
) -> BoschRuntimeData:
    """Build runtime data backed by lightweight fakes."""
    return BoschRuntimeData(
        coordinator=cast(Any, _FakeCoordinator(endpoints)),
        gateway_uuid="test-gateway",
        host="192.0.2.1",
        h60=cast(Any, h60),
    )


def test_extra_warm_water_uses_native_charge_commands() -> None:
    """The dedicated switch sends the gateway's start and stop commands."""
    runtime = _runtime([_endpoint(_CHARGE_PATH, "stop", ("stop", "start"))])
    entity = BoschExtraWarmWaterSwitch(runtime)

    assert entity.is_on is False
    assert entity.name == "Extra warm water"
    assert entity.unique_id == "test-gateway_extra_warm_water"
    assert (
        entity.internal_integration_suggested_object_id
        == "bosch_compress_extra_warm_water"
    )
    assert entity.entity_registry_enabled_default is True

    asyncio.run(entity.async_turn_on())
    asyncio.run(entity.async_turn_off())

    assert runtime.coordinator.writes == [
        (_CHARGE_PATH, "start"),
        (_CHARGE_PATH, "stop"),
    ]


def test_dhw_program_maps_friendly_labels_to_native_values() -> None:
    """Every known program label round-trips through its Bosch API value."""
    runtime = _runtime(
        [
            _endpoint(
                _PROGRAM_PATH,
                "high",
                ("Off", "low", "high", "ownprogram", "eco"),
            )
        ]
    )
    entity = BoschDhwProgramSelect(runtime)

    assert entity.options == [
        "Off",
        "Eco / Normal",
        "Comfort",
        "Manual time program",
        "Eco+",
    ]
    assert entity.current_option == "Comfort"
    assert entity.name == "DHW program"
    assert entity.unique_id == "test-gateway_dhw_program"
    assert (
        entity.internal_integration_suggested_object_id == "bosch_compress_dhw_program"
    )
    assert entity.entity_registry_enabled_default is True

    for option, native_value in (
        ("Off", "Off"),
        ("Eco / Normal", "low"),
        ("Comfort", "high"),
        ("Manual time program", "ownprogram"),
        ("Eco+", "eco"),
    ):
        asyncio.run(entity.async_select_option(option))
        assert runtime.coordinator.writes[-1] == (_PROGRAM_PATH, native_value)


def test_dhw_program_only_offers_values_reported_by_endpoint() -> None:
    """A gateway-specific subset and future raw mode remain selectable."""
    runtime = _runtime(
        [_endpoint(_PROGRAM_PATH, "futureMode", ("Off", "eco", "futureMode"))]
    )
    entity = BoschDhwProgramSelect(runtime)

    assert entity.options == ["Off", "Eco+", "futureMode"]
    assert entity.current_option == "futureMode"

    asyncio.run(entity.async_select_option("futureMode"))
    assert runtime.coordinator.writes == [(_PROGRAM_PATH, "futureMode")]


def test_native_dhw_controls_replace_only_their_h60_fallbacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Native endpoints win without creating generic or H60 duplicates."""
    runtime = _runtime(
        [
            _endpoint(_CHARGE_PATH, "stop", ("stop", "start")),
            _endpoint(
                _PROGRAM_PATH,
                "eco",
                ("Off", "low", "high", "ownprogram", "eco"),
            ),
        ],
        h60=_FakeH60(),
    )
    config_entry = cast(Any, SimpleNamespace(runtime_data=runtime))
    registry = _FakeEntityRegistry(
        {
            (
                "switch",
                DOMAIN,
                "test-gateway_h60_1231",
            ): "switch.bosch_compress_extra_warm_water",
            (
                "select",
                DOMAIN,
                "test-gateway_h60_2213",
            ): "select.bosch_compress_dhw_program",
        }
    )
    monkeypatch.setattr(switch_platform.er, "async_get", lambda _hass: registry)
    switches: list[Any] = []
    selects: list[Any] = []

    hass = cast(Any, object())
    asyncio.run(async_setup_switches(hass, config_entry, switches.extend))
    asyncio.run(async_setup_selects(hass, config_entry, selects.extend))

    assert (
        sum(isinstance(entity, BoschExtraWarmWaterSwitch) for entity in switches) == 1
    )
    assert sum(isinstance(entity, BoschDhwProgramSelect) for entity in selects) == 1
    assert not any(
        isinstance(entity, H60SupplementalSwitch)
        and entity._description is H60_REGISTERS["1231"]
        for entity in switches
    )
    assert not any(
        isinstance(entity, H60SupplementalSelect)
        and entity._description is H60_REGISTERS["2213"]
        for entity in selects
    )
    assert registry.updates == [
        (
            "switch.bosch_compress_extra_warm_water",
            "test-gateway_extra_warm_water",
        ),
        ("select.bosch_compress_dhw_program", "test-gateway_dhw_program"),
    ]


def test_native_registry_migration_does_not_overwrite_existing_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pre-existing native identity is never replaced by the legacy entry."""
    runtime = _runtime(
        [
            _endpoint(_CHARGE_PATH, "stop", ("stop", "start")),
            _endpoint(_PROGRAM_PATH, "eco", ("Off", "eco")),
        ]
    )
    config_entry = cast(Any, SimpleNamespace(runtime_data=runtime))
    registry = _FakeEntityRegistry(
        {
            (
                "switch",
                DOMAIN,
                "test-gateway_h60_1231",
            ): "switch.bosch_compress_extra_warm_water",
            (
                "switch",
                DOMAIN,
                "test-gateway_extra_warm_water",
            ): "switch.bosch_compress_extra_warm_water_2",
            (
                "select",
                DOMAIN,
                "test-gateway_h60_2213",
            ): "select.bosch_compress_dhw_program",
            (
                "select",
                DOMAIN,
                "test-gateway_dhw_program",
            ): "select.bosch_compress_dhw_program_2",
        }
    )
    monkeypatch.setattr(select_platform.er, "async_get", lambda _hass: registry)

    hass = cast(Any, object())
    asyncio.run(async_setup_switches(hass, config_entry, cast(Any, lambda _: None)))
    asyncio.run(async_setup_selects(hass, config_entry, cast(Any, lambda _: None)))

    assert registry.updates == []


def test_h60_controls_remain_fallbacks_when_native_paths_are_absent() -> None:
    """The temporary supplemental controls remain until native discovery succeeds."""
    runtime = _runtime([], h60=_FakeH60())
    config_entry = cast(Any, SimpleNamespace(runtime_data=runtime))
    switches: list[Any] = []
    selects: list[Any] = []

    asyncio.run(async_setup_switches(cast(Any, None), config_entry, switches.extend))
    asyncio.run(async_setup_selects(cast(Any, None), config_entry, selects.extend))

    assert any(
        isinstance(entity, H60SupplementalSwitch)
        and entity._description is H60_REGISTERS["1231"]
        for entity in switches
    )
    assert any(
        isinstance(entity, H60SupplementalSelect)
        and entity._description is H60_REGISTERS["2213"]
        for entity in selects
    )


def test_read_only_native_paths_do_not_replace_h60_fallbacks() -> None:
    """Discovery without native write access cannot trigger a migration."""
    runtime = _runtime(
        [
            _endpoint(
                _CHARGE_PATH,
                "stop",
                ("stop", "start"),
                writable=False,
            ),
            _endpoint(
                _PROGRAM_PATH,
                "eco",
                ("Off", "eco"),
                writable=False,
            ),
        ],
        h60=_FakeH60(),
    )
    config_entry = cast(Any, SimpleNamespace(runtime_data=runtime))
    switches: list[Any] = []
    selects: list[Any] = []

    # Passing no Home Assistant object also proves registry access is conditional.
    asyncio.run(async_setup_switches(cast(Any, None), config_entry, switches.extend))
    asyncio.run(async_setup_selects(cast(Any, None), config_entry, selects.extend))

    assert any(
        isinstance(entity, H60SupplementalSwitch)
        and entity._description is H60_REGISTERS["1231"]
        for entity in switches
    )
    assert any(
        isinstance(entity, H60SupplementalSelect)
        and entity._description is H60_REGISTERS["2213"]
        for entity in selects
    )


def test_charge_requires_advertised_start_and_stop_values() -> None:
    """Do not assume charge semantics from the endpoint path alone."""
    runtime = _runtime(
        [_endpoint(_CHARGE_PATH, "disabled", ("disabled", "enabled"))],
        h60=_FakeH60(),
    )
    config_entry = cast(Any, SimpleNamespace(runtime_data=runtime))
    switches: list[Any] = []

    asyncio.run(
        async_setup_switches(cast(Any, None), config_entry, switches.extend)
    )

    assert not any(
        isinstance(entity, BoschExtraWarmWaterSwitch) for entity in switches
    )
    assert any(
        isinstance(entity, H60SupplementalSwitch)
        and entity._description is H60_REGISTERS["1231"]
        for entity in switches
    )
