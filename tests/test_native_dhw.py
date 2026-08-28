"""Tests for Bosch-native domestic-hot-water controls."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast

import pytest
from homeassistant.components.water_heater import WaterHeaterEntityFeature
from homeassistant.exceptions import HomeAssistantError

from custom_components.bosch_compress.models import BoschEndpoint
from custom_components.bosch_compress.runtime import BoschRuntimeData
from custom_components.bosch_compress.select import (
    BoschDhwProgramSelect,
)
from custom_components.bosch_compress.select import (
    async_setup_entry as async_setup_selects,
)
from custom_components.bosch_compress.switch import (
    BoschExtraWarmWaterSwitch,
)
from custom_components.bosch_compress.switch import (
    async_setup_entry as async_setup_switches,
)
from custom_components.bosch_compress.topology import BoschTopology
from custom_components.bosch_compress.water_heater import (
    BoschDhwWaterHeater,
)
from custom_components.bosch_compress.water_heater import (
    async_setup_entry as async_setup_water_heaters,
)

_ACTUAL_TEMP_PATH = "/dhwCircuits/dhw1/actualTemp"
_CHARGE_PATH = "/dhwCircuits/dhw1/charge"
_CURRENT_SETPOINT_PATH = "/dhwCircuits/dhw1/currentSetpoint"
_PROGRAM_PATH = "/dhwCircuits/dhw1/operationMode"
_STATUS_PATH = "/dhwCircuits/dhw1/status"
_ALL_PROGRAMS = ("Off", "low", "high", "ownprogram", "eco")
_CANONICAL_PROGRAMS = [
    "Off",
    "Eco / Normal",
    "Comfort",
    "Manual time program",
    "Eco+",
]


class _FakeCoordinator:
    """Minimal coordinator for native DHW entity tests."""

    def __init__(self, endpoints: list[BoschEndpoint]) -> None:
        self.last_update_success = True
        self.discovered_endpoints = {endpoint.path: endpoint for endpoint in endpoints}
        self.writes: list[tuple[str, Any]] = []
        self.registered_paths: set[str] = set()

    def endpoint(self, path: str) -> BoschEndpoint | None:
        """Return one endpoint snapshot."""
        return self.discovered_endpoints.get(path)

    async def async_write(self, path: str, value: Any) -> None:
        """Record one native gateway write."""
        self.writes.append((path, value))

    def async_add_listener(self, update_callback: Any, context: Any = None) -> Any:
        """Return a no-op coordinator unsubscribe callback."""
        del update_callback
        if isinstance(context, str):
            self.registered_paths.add(context)
        return lambda: None

    def register_path(self, path: str) -> Any:
        """Record a path that the real coordinator will retry each refresh."""
        self.registered_paths.add(path)
        return lambda: None

    def add_endpoints(self, *endpoints: BoschEndpoint) -> None:
        """Make endpoints appear after an initially incomplete boot poll."""
        self.discovered_endpoints.update(
            {endpoint.path: endpoint for endpoint in endpoints}
        )


def _endpoint(
    path: str,
    value: Any,
    allowed_values: tuple[Any, ...] = (),
    *,
    writable: bool = False,
) -> BoschEndpoint:
    """Build one Bosch endpoint."""
    return BoschEndpoint(
        path=path,
        value=value,
        value_type=("floatValue" if isinstance(value, int | float) else "stringValue"),
        writable=writable,
        allowed_values=allowed_values,
    )


def _runtime(endpoints: list[BoschEndpoint]) -> BoschRuntimeData:
    """Build runtime data backed by a lightweight coordinator."""
    return BoschRuntimeData(
        coordinator=cast(Any, _FakeCoordinator(endpoints)),
        gateway_uuid="test-gateway",
        host="192.0.2.1",
    )


def test_extra_warm_water_uses_native_charge_commands() -> None:
    """The dedicated switch sends the gateway's start and stop commands."""
    runtime = _runtime(
        [_endpoint(_CHARGE_PATH, "stop", ("stop", "start"), writable=True)]
    )
    entity = BoschExtraWarmWaterSwitch(runtime)

    assert entity.available is True
    assert entity.is_on is False
    assert entity.name == "Extra warm water"
    assert entity.unique_id == "test-gateway_extra_warm_water"
    assert not hasattr(entity, "internal_integration_suggested_object_id")
    assert entity.entity_registry_enabled_default is True

    asyncio.run(entity.async_turn_on())
    asyncio.run(entity.async_turn_off())

    assert runtime.coordinator.writes == [
        (_CHARGE_PATH, "start"),
        (_CHARGE_PATH, "stop"),
    ]


def test_dhw_program_maps_friendly_labels_to_native_values() -> None:
    """Every known program label round-trips through its Bosch API value."""
    runtime = _runtime([_endpoint(_PROGRAM_PATH, "high", _ALL_PROGRAMS, writable=True)])
    entity = BoschDhwProgramSelect(runtime)

    assert entity.available is True
    assert entity.options == _CANONICAL_PROGRAMS
    assert entity.current_option == "Comfort"
    assert entity.name == "DHW program"
    assert entity.unique_id == "test-gateway_dhw_program"
    assert not hasattr(entity, "internal_integration_suggested_object_id")
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
        [
            _endpoint(
                _PROGRAM_PATH,
                "futureMode",
                ("Off", "eco", "futureMode"),
                writable=True,
            )
        ]
    )
    entity = BoschDhwProgramSelect(runtime)

    assert entity.options == ["Off", "Eco+", "futureMode"]
    assert entity.current_option == "futureMode"

    asyncio.run(entity.async_select_option("futureMode"))
    assert runtime.coordinator.writes == [(_PROGRAM_PATH, "futureMode")]


@pytest.mark.parametrize("circuit_count", range(3))
def test_setup_enumerates_zero_to_two_discovered_dhw_circuits(
    circuit_count: int,
) -> None:
    """Each discovered DHW capability creates exactly one matching entity."""
    endpoints: list[BoschEndpoint] = []
    for number in range(1, circuit_count + 1):
        circuit_id = f"dhw{number}"
        base = f"/dhwCircuits/{circuit_id}"
        endpoints.extend(
            (
                _endpoint(f"{base}/actualTemp", 45.0 + number),
                _endpoint(f"{base}/currentSetpoint", 52.0),
                _endpoint(f"{base}/status", "INACTIVE"),
                _endpoint(
                    f"{base}/charge",
                    "stop",
                    ("stop", "start"),
                    writable=True,
                ),
                _endpoint(
                    f"{base}/operationMode",
                    "eco",
                    _ALL_PROGRAMS,
                    writable=True,
                ),
            )
        )
    runtime = _runtime(endpoints)
    config_entry = cast(Any, SimpleNamespace(runtime_data=runtime))
    switches: list[BoschExtraWarmWaterSwitch] = []
    selects: list[BoschDhwProgramSelect] = []
    water_heaters: list[BoschDhwWaterHeater] = []

    asyncio.run(async_setup_switches(cast(Any, None), config_entry, switches.extend))
    asyncio.run(async_setup_selects(cast(Any, None), config_entry, selects.extend))
    asyncio.run(
        async_setup_water_heaters(cast(Any, None), config_entry, water_heaters.extend)
    )

    assert [entity.unique_id for entity in water_heaters] == [
        f"test-gateway_dhw{number}" for number in range(1, circuit_count + 1)
    ]
    assert [entity.unique_id for entity in switches] == [
        (
            "test-gateway_extra_warm_water"
            if number == 1
            else f"test-gateway_dhw{number}_extra_warm_water"
        )
        for number in range(1, circuit_count + 1)
    ]
    assert [entity.unique_id for entity in selects] == [
        (
            "test-gateway_dhw_program"
            if number == 1
            else f"test-gateway_dhw{number}_program"
        )
        for number in range(1, circuit_count + 1)
    ]

    if circuit_count:
        asyncio.run(switches[-1].async_turn_on())
        asyncio.run(selects[-1].async_select_option("Comfort"))
        assert runtime.coordinator.writes[-2:] == [
            (f"/dhwCircuits/dhw{circuit_count}/charge", "start"),
            (f"/dhwCircuits/dhw{circuit_count}/operationMode", "high"),
        ]


def test_dhw_control_capabilities_do_not_fabricate_water_heater() -> None:
    """Charge-only hardware exposes its switch without an unavailable water heater."""
    runtime = _runtime(
        [
            _endpoint(
                "/dhwCircuits/tank/charge",
                "stop",
                ("stop", "start"),
                writable=True,
            )
        ]
    )
    config_entry = cast(Any, SimpleNamespace(runtime_data=runtime))
    switches: list[Any] = []
    selects: list[Any] = []
    water_heaters: list[Any] = []

    asyncio.run(async_setup_switches(cast(Any, None), config_entry, switches.extend))
    asyncio.run(async_setup_selects(cast(Any, None), config_entry, selects.extend))
    asyncio.run(
        async_setup_water_heaters(cast(Any, None), config_entry, water_heaters.extend)
    )

    assert [entity.unique_id for entity in switches] == [
        "test-gateway_tank_extra_warm_water"
    ]
    assert selects == []
    assert water_heaters == []


def test_temperature_only_dhw_circuit_is_available_read_only() -> None:
    """A minimal DHW circuit remains useful without optional status or setpoint."""
    runtime = _runtime([_endpoint("/dhwCircuits/tank/actualTemp", 47.5)])
    config_entry = cast(Any, SimpleNamespace(runtime_data=runtime))
    water_heaters: list[BoschDhwWaterHeater] = []

    asyncio.run(
        async_setup_water_heaters(cast(Any, None), config_entry, water_heaters.extend)
    )

    assert len(water_heaters) == 1
    assert water_heaters[0].available is True
    assert water_heaters[0].current_temperature == 47.5
    assert water_heaters[0].target_temperature is None
    assert water_heaters[0].current_operation is None


def test_boot_missing_dhw_endpoints_recover_in_place() -> None:
    """Stable DHW entities retry missing paths and recover without duplicates."""
    runtime = _runtime([])
    runtime.topology = BoschTopology(
        dhw_circuits=frozenset({"dhw1"}),
        dhw_charge_circuits=frozenset({"dhw1"}),
        dhw_program_circuits=frozenset({"dhw1"}),
    )
    coordinator = cast(_FakeCoordinator, runtime.coordinator)
    config_entry = cast(Any, SimpleNamespace(runtime_data=runtime))
    switches: list[BoschExtraWarmWaterSwitch] = []
    selects: list[BoschDhwProgramSelect] = []
    water_heaters: list[BoschDhwWaterHeater] = []

    asyncio.run(async_setup_switches(cast(Any, None), config_entry, switches.extend))
    asyncio.run(async_setup_selects(cast(Any, None), config_entry, selects.extend))
    asyncio.run(
        async_setup_water_heaters(cast(Any, None), config_entry, water_heaters.extend)
    )

    assert len(switches) == len(selects) == len(water_heaters) == 1
    assert switches[0].available is False
    assert switches[0].is_on is None
    assert selects[0].available is False
    assert selects[0].options == []
    assert selects[0].current_option is None
    assert water_heaters[0].available is False

    with pytest.raises(HomeAssistantError, match="unavailable"):
        asyncio.run(switches[0].async_turn_on())
    with pytest.raises(HomeAssistantError, match="Unsupported DHW program"):
        asyncio.run(selects[0].async_select_option("Comfort"))

    async def add_entities_to_hass() -> None:
        for entity in (*switches, *selects, *water_heaters):
            await entity.async_added_to_hass()

    asyncio.run(add_entities_to_hass())

    assert {
        _ACTUAL_TEMP_PATH,
        _CHARGE_PATH,
        _CURRENT_SETPOINT_PATH,
        _PROGRAM_PATH,
        _STATUS_PATH,
    }.issubset(coordinator.registered_paths)

    coordinator.add_endpoints(
        _endpoint(_CHARGE_PATH, "start", ("stop", "start"), writable=True),
        _endpoint(_PROGRAM_PATH, "low", _ALL_PROGRAMS, writable=True),
        _endpoint(_ACTUAL_TEMP_PATH, 48.25),
        _endpoint(_CURRENT_SETPOINT_PATH, 54.0),
        _endpoint(_STATUS_PATH, "ACTIVE"),
    )

    assert switches[0].available is True
    assert switches[0].is_on is True
    assert selects[0].available is True
    assert selects[0].current_option == "Eco / Normal"
    assert water_heaters[0].available is True
    assert water_heaters[0].current_temperature == 48.25
    assert water_heaters[0].target_temperature == 54.0
    assert water_heaters[0].operation_list == _CANONICAL_PROGRAMS
    assert water_heaters[0].current_operation == "Eco / Normal"
    assert (
        water_heaters[0].supported_features == WaterHeaterEntityFeature.OPERATION_MODE
    )

    asyncio.run(water_heaters[0].async_set_operation_mode("Comfort"))
    assert coordinator.writes[-1] == (_PROGRAM_PATH, "high")


def test_read_only_native_dhw_reports_activity_as_an_attribute_only() -> None:
    """Read-only control paths do not claim setters or create generic entities."""
    runtime = _runtime(
        [
            _endpoint(_CHARGE_PATH, "stop", ("stop", "start")),
            _endpoint(_PROGRAM_PATH, "eco", _ALL_PROGRAMS),
            _endpoint(_ACTUAL_TEMP_PATH, 48.25),
            _endpoint(_CURRENT_SETPOINT_PATH, 54.0),
            _endpoint(_STATUS_PATH, "ACTIVE"),
        ]
    )
    config_entry = cast(Any, SimpleNamespace(runtime_data=runtime))
    switches: list[Any] = []
    selects: list[Any] = []
    water_heaters: list[BoschDhwWaterHeater] = []

    asyncio.run(async_setup_switches(cast(Any, None), config_entry, switches.extend))
    asyncio.run(async_setup_selects(cast(Any, None), config_entry, selects.extend))
    asyncio.run(
        async_setup_water_heaters(cast(Any, None), config_entry, water_heaters.extend)
    )

    assert [type(entity) for entity in switches] == [BoschExtraWarmWaterSwitch]
    assert [type(entity) for entity in selects] == [BoschDhwProgramSelect]
    assert switches[0].available is False
    assert selects[0].available is False
    assert water_heaters[0].supported_features == WaterHeaterEntityFeature(0)
    assert water_heaters[0].operation_list is None
    assert water_heaters[0].current_operation is None
    assert water_heaters[0].extra_state_attributes == {
        "bosch_status": "ACTIVE",
        "target_is_read_only": True,
    }
    assert not (
        water_heaters[0].supported_features
        & WaterHeaterEntityFeature.TARGET_TEMPERATURE
    )

    with pytest.raises(HomeAssistantError, match="Unsupported DHW program"):
        asyncio.run(water_heaters[0].async_set_operation_mode("Eco+"))


def test_charge_requires_advertised_start_and_stop_values() -> None:
    """The stable switch stays unavailable for an incompatible charge endpoint."""
    runtime = _runtime(
        [
            _endpoint(
                _CHARGE_PATH,
                "disabled",
                ("disabled", "enabled"),
                writable=True,
            )
        ]
    )
    config_entry = cast(Any, SimpleNamespace(runtime_data=runtime))
    switches: list[Any] = []

    asyncio.run(async_setup_switches(cast(Any, None), config_entry, switches.extend))

    assert [type(entity) for entity in switches] == [BoschExtraWarmWaterSwitch]
    assert switches[0].available is False

    with pytest.raises(HomeAssistantError, match="unavailable"):
        asyncio.run(switches[0].async_turn_on())
