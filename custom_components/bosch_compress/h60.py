"""Optional Husdata H60 MQTT transport for Bosch-only capability gaps.

The H60 gateway exposes controller registers as unscaled numeric MQTT payloads.
This module deliberately keeps that wire protocol separate from Home Assistant
entity modelling: callers decide whether a value belongs in a climate, select,
switch, or diagnostic entity.
"""

from __future__ import annotations

import importlib
import logging
import math
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Final, Protocol

from homeassistant.const import PERCENTAGE, UnitOfPower, UnitOfTemperature
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback

_LOGGER = logging.getLogger(__name__)

type H60Value = int | float
type H60Listener = Callable[[str, H60Value], None]


class H60RegisterKind(StrEnum):
    """Suggested Home Assistant representation for an H60 register."""

    BINARY_SENSOR = "binary_sensor"
    NUMBER = "number"
    SELECT = "select"
    SENSOR = "sensor"
    SWITCH = "switch"


@dataclass(frozen=True, slots=True)
class H60RegisterDescription:
    """Describe one H60 register without coupling it to an entity class."""

    register: str
    key: str
    name: str
    kind: H60RegisterKind
    unit: str | None = None
    writable: bool = False
    native_min_value: float | None = None
    native_max_value: float | None = None
    native_step: float | None = None
    options: tuple[tuple[int, str], ...] = ()
    enabled_by_default: bool = False
    description: str = ""

    @property
    def option_values(self) -> tuple[int, ...]:
        """Return raw numeric values accepted by an enumerated register."""
        return tuple(value for value, _label in self.options)


def _register(
    register: str,
    key: str,
    name: str,
    kind: H60RegisterKind,
    **kwargs: Any,
) -> H60RegisterDescription:
    """Build a register descriptor while keeping the catalog compact."""
    return H60RegisterDescription(register, key, name, kind, **kwargs)


_REGISTER_DESCRIPTIONS: Final = (
    _register(
        "0005",
        "evaporator_temperature",
        "Evaporator temperature",
        H60RegisterKind.SENSOR,
        unit=UnitOfTemperature.CELSIUS,
        enabled_by_default=True,
        description="Brine-in temperature on ground-source models.",
    ),
    _register(
        "0006",
        "condenser_temperature",
        "Condenser temperature",
        H60RegisterKind.SENSOR,
        unit=UnitOfTemperature.CELSIUS,
        enabled_by_default=True,
        description="Brine-out temperature on ground-source models.",
    ),
    _register(
        "000A",
        "dhw_middle_temperature",
        "DHW middle temperature",
        H60RegisterKind.SENSOR,
        unit=UnitOfTemperature.CELSIUS,
        enabled_by_default=True,
    ),
    _register(
        "000B",
        "hot_gas_temperature",
        "Hot gas temperature",
        H60RegisterKind.SENSOR,
        unit=UnitOfTemperature.CELSIUS,
        enabled_by_default=True,
        description="Compressor discharge temperature before the expansion valve.",
    ),
    _register(
        "000C",
        "suction_gas_temperature",
        "Suction gas temperature",
        H60RegisterKind.SENSOR,
        unit=UnitOfTemperature.CELSIUS,
        enabled_by_default=True,
    ),
    _register(
        "000E",
        "air_intake_temperature",
        "Air intake temperature",
        H60RegisterKind.SENSOR,
        unit=UnitOfTemperature.CELSIUS,
        enabled_by_default=True,
    ),
    _register(
        "0011",
        "pool_temperature",
        "Pool temperature",
        H60RegisterKind.SENSOR,
        unit=UnitOfTemperature.CELSIUS,
        enabled_by_default=True,
    ),
    _register(
        "0203",
        "hc1_manual_room_setpoint",
        "HC1 manual room temperature target",
        H60RegisterKind.NUMBER,
        unit=UnitOfTemperature.CELSIUS,
        writable=True,
        native_min_value=13,
        native_max_value=29,
        native_step=0.5,
        enabled_by_default=True,
        description="Room target used in Manual/Optimized operating mode.",
    ),
    _register(
        "0205",
        "hc1_heat_curve_left_raw",
        "HC1 heat curve left value (raw)",
        H60RegisterKind.NUMBER,
        writable=True,
        native_min_value=6,
        native_max_value=30,
        native_step=0.5,
        enabled_by_default=True,
        description=(
            "Range preserved from MQTT discovery; the precise controller semantics "
            "of this legacy CurveL value are unverified."
        ),
    ),
    _register(
        "0206",
        "hc2_heat_curve_right_raw",
        "HC2 heat curve right value (raw)",
        H60RegisterKind.NUMBER,
        writable=True,
        native_min_value=5,
        native_max_value=25,
        native_step=0.5,
        enabled_by_default=True,
        description=(
            "Range preserved from MQTT discovery; the precise controller semantics "
            "of this legacy CurveR value are unverified."
        ),
    ),
    _register(
        "020C",
        "hc1_program_room_setpoint",
        "HC1 program room temperature target",
        H60RegisterKind.NUMBER,
        unit=UnitOfTemperature.CELSIUS,
        writable=True,
        native_min_value=13,
        native_max_value=29,
        native_step=0.5,
        description="Room target used in automatic/time-program operating mode.",
    ),
    _register(
        "0220",
        "hc2_room_setpoint",
        "HC2 room temperature target",
        H60RegisterKind.NUMBER,
        unit=UnitOfTemperature.CELSIUS,
        writable=True,
        native_min_value=13,
        native_max_value=29,
        native_step=0.5,
        description="Room target for heating circuit 2.",
    ),
    _register(
        "1231",
        "extra_warm_water",
        "Extra warm water",
        H60RegisterKind.SWITCH,
        writable=True,
        native_min_value=0,
        native_max_value=1,
        native_step=1,
        enabled_by_default=True,
        description="0 disables and 1 starts the controller's timed DHW boost.",
    ),
    _register(
        "1A04",
        "source_circuit_pump",
        "Source circuit pump",
        H60RegisterKind.BINARY_SENSOR,
        native_min_value=0,
        native_max_value=1,
        enabled_by_default=True,
        description="0 is off and 1 is on.",
    ),
    _register(
        "1A05",
        "heat_circuit_pump",
        "Heat circuit pump",
        H60RegisterKind.BINARY_SENSOR,
        native_min_value=0,
        native_max_value=1,
        enabled_by_default=True,
        description="0 is off and 1 is on.",
    ),
    _register(
        "1A06",
        "radiator_pump",
        "Radiator pump",
        H60RegisterKind.BINARY_SENSOR,
        native_min_value=0,
        native_max_value=1,
        enabled_by_default=True,
        description="0 is off and 1 is on.",
    ),
    _register(
        "1A07",
        "diverter_valve",
        "Diverter valve",
        H60RegisterKind.BINARY_SENSOR,
        native_min_value=0,
        native_max_value=1,
        enabled_by_default=True,
        description="0 selects space heating and 1 selects domestic hot water.",
    ),
    _register(
        "1A0C",
        "heating_cable",
        "Heating cable",
        H60RegisterKind.BINARY_SENSOR,
        native_min_value=0,
        native_max_value=1,
        enabled_by_default=True,
        description="0 is off and 1 is on.",
    ),
    _register(
        "1A20",
        "active_alarm",
        "Active alarm",
        H60RegisterKind.BINARY_SENSOR,
        native_min_value=0,
        native_max_value=1,
        enabled_by_default=True,
        description="Live controller alarm state; 1 means an alarm is active.",
    ),
    _register(
        "2201",
        "hc1_operating_mode_raw",
        "HC1 operating mode (raw)",
        H60RegisterKind.NUMBER,
        writable=True,
        native_min_value=0,
        native_max_value=1,
        native_step=1,
        enabled_by_default=True,
        description=(
            "Raw H60 mode value. The meaning of each numeric value is unverified."
        ),
    ),
    _register(
        "2202",
        "hc2_operating_mode_raw",
        "HC2 operating mode (raw)",
        H60RegisterKind.NUMBER,
        writable=True,
        native_min_value=0,
        native_max_value=2,
        native_step=1,
        enabled_by_default=True,
        description=(
            "Raw H60 mode value. The meaning of each numeric value is unverified."
        ),
    ),
    _register(
        "2204",
        "hc1_room_sensor_influence_raw",
        "HC1 room sensor influence (raw)",
        H60RegisterKind.NUMBER,
        writable=True,
        native_min_value=0,
        native_max_value=10,
        native_step=1,
        enabled_by_default=True,
        description=(
            "Range preserved from MQTT discovery; exact controller semantics are "
            "unverified."
        ),
    ),
    _register(
        "2213",
        "dhw_program",
        "DHW program",
        H60RegisterKind.SELECT,
        writable=True,
        native_min_value=0,
        native_max_value=5,
        native_step=1,
        options=(
            (0, "Off"),
            (1, "Eco / Normal"),
            (2, "Comfort"),
            (4, "Manual time program"),
            (5, "Eco+"),
        ),
        enabled_by_default=True,
        description="Manual is the controller's time-program mode; value 3 is invalid.",
    ),
    _register(
        "2215",
        "electric_heater_enabled",
        "Electric heater enabled",
        H60RegisterKind.SWITCH,
        writable=True,
        native_min_value=0,
        native_max_value=1,
        native_step=1,
        enabled_by_default=True,
        description="0 disables and 1 enables the electric heater setting.",
    ),
    _register(
        "2227",
        "compressor_limit_raw",
        "Compressor limit (raw)",
        H60RegisterKind.SENSOR,
        enabled_by_default=True,
        description="Raw controller limit value; unit and semantics are unverified.",
    ),
    _register(
        "2228",
        "auxiliary_heat_limit_raw",
        "Auxiliary heat limit (raw)",
        H60RegisterKind.SENSOR,
        enabled_by_default=True,
        description="Raw controller limit value; unit and semantics are unverified.",
    ),
    _register(
        "2229",
        "dhw_limit_raw",
        "DHW limit (raw)",
        H60RegisterKind.SENSOR,
        enabled_by_default=True,
        description="Raw controller limit value; unit and semantics are unverified.",
    ),
    _register(
        "2233",
        "hc1_external_control",
        "HC1 external control",
        H60RegisterKind.SWITCH,
        writable=True,
        native_min_value=0,
        native_max_value=1,
        native_step=1,
        enabled_by_default=True,
        description=(
            "0 and 1 are preserved from MQTT discovery; exact controller behavior "
            "must be verified before relying on this setting."
        ),
    ),
    _register(
        "2234",
        "hc2_external_control",
        "HC2 external control",
        H60RegisterKind.SWITCH,
        writable=True,
        native_min_value=0,
        native_max_value=1,
        native_step=1,
        enabled_by_default=True,
        description=(
            "0 and 1 are preserved from MQTT discovery; exact controller behavior "
            "must be verified before relying on this setting."
        ),
    ),
    _register(
        "2A91",
        "alarm_code",
        "Alarm code",
        H60RegisterKind.SENSOR,
        enabled_by_default=True,
        description="Current H60 alarm code; the live gateway publishes 0 when clear.",
    ),
    _register(
        "2C61",
        "compressor_starts",
        "Compressor starts",
        H60RegisterKind.SENSOR,
        enabled_by_default=True,
    ),
    _register(
        "3104",
        "auxiliary_heater_output",
        "Auxiliary heater output",
        H60RegisterKind.SENSOR,
        unit=PERCENTAGE,
        native_min_value=0,
        native_max_value=100,
        enabled_by_default=True,
    ),
    _register(
        "3109",
        "circulation_pump_speed",
        "Circulation pump speed",
        H60RegisterKind.SENSOR,
        unit=PERCENTAGE,
        native_min_value=0,
        native_max_value=100,
        enabled_by_default=True,
    ),
    _register(
        "3110",
        "source_pump_speed",
        "Source pump speed",
        H60RegisterKind.SENSOR,
        unit=PERCENTAGE,
        native_min_value=0,
        native_max_value=100,
        enabled_by_default=True,
        description="Legacy MQTT discovery labels this value as brine pump speed.",
    ),
    _register(
        "3120",
        "hc2_heating_setpoint_raw",
        "HC2 heating setpoint (raw)",
        H60RegisterKind.SENSOR,
        unit=PERCENTAGE,
        enabled_by_default=True,
        description=(
            "The percent unit is preserved from MQTT discovery; exact controller "
            "semantics are unverified."
        ),
    ),
    _register(
        "3128",
        "hc2_shunt_position",
        "HC2 shunt position",
        H60RegisterKind.SENSOR,
        unit=PERCENTAGE,
        native_min_value=0,
        native_max_value=100,
        enabled_by_default=True,
    ),
    _register(
        "5C51",
        "supplemental_energy_total_raw",
        "Supplemental energy total (raw)",
        H60RegisterKind.SENSOR,
        enabled_by_default=True,
        description="Legacy cumulative value; its unit is not declared by discovery.",
    ),
    _register(
        "5C52",
        "supplemental_energy_heating_raw",
        "Supplemental energy heating (raw)",
        H60RegisterKind.SENSOR,
        enabled_by_default=True,
        description="Legacy cumulative value; its unit is not declared by discovery.",
    ),
    _register(
        "5C53",
        "supplemental_energy_dhw_raw",
        "Supplemental energy DHW (raw)",
        H60RegisterKind.SENSOR,
        enabled_by_default=True,
        description="Legacy cumulative value; its unit is not declared by discovery.",
    ),
    _register(
        "5C54",
        "compressor_consumption_total_raw",
        "Compressor consumption total (raw)",
        H60RegisterKind.SENSOR,
        enabled_by_default=True,
        description="Legacy cumulative value; its unit is not declared by discovery.",
    ),
    _register(
        "5C55",
        "compressor_consumption_heating_raw",
        "Compressor consumption heating (raw)",
        H60RegisterKind.SENSOR,
        enabled_by_default=True,
        description="Legacy cumulative value; its unit is not declared by discovery.",
    ),
    _register(
        "5C56",
        "compressor_consumption_dhw_raw",
        "Compressor consumption DHW (raw)",
        H60RegisterKind.SENSOR,
        enabled_by_default=True,
        description="Legacy cumulative value; its unit is not declared by discovery.",
    ),
    _register(
        "5C57",
        "auxiliary_consumption_total_raw",
        "Auxiliary consumption total (raw)",
        H60RegisterKind.SENSOR,
        enabled_by_default=True,
        description="Legacy cumulative value; its unit is not declared by discovery.",
    ),
    _register(
        "5C58",
        "auxiliary_consumption_heating_raw",
        "Auxiliary consumption heating (raw)",
        H60RegisterKind.SENSOR,
        enabled_by_default=True,
        description="Legacy cumulative value; its unit is not declared by discovery.",
    ),
    _register(
        "5C59",
        "auxiliary_consumption_dhw_raw",
        "Auxiliary consumption DHW (raw)",
        H60RegisterKind.SENSOR,
        enabled_by_default=True,
        description="Legacy cumulative value; its unit is not declared by discovery.",
    ),
    _register(
        "6C50",
        "total_operation_time_raw",
        "Total operation time (raw)",
        H60RegisterKind.SENSOR,
        enabled_by_default=True,
        description="Legacy cumulative value; its unit is not declared by discovery.",
    ),
    _register(
        "6C60",
        "compressor_runtime_raw",
        "Compressor runtime (raw)",
        H60RegisterKind.SENSOR,
        enabled_by_default=True,
        description="Legacy cumulative value; its unit is not declared by discovery.",
    ),
    # The installed H60 publishes this value as 9108/kW. Newer Husdata C10
    # documentation calls the equivalent register 9112, so keep the observed
    # wire identifier until a live migration can be verified safely.
    _register(
        "9108",
        "compressor_power",
        "Compressor electrical power",
        H60RegisterKind.SENSOR,
        unit=UnitOfPower.KILO_WATT,
        enabled_by_default=True,
    ),
)

H60_REGISTERS: Final[Mapping[str, H60RegisterDescription]] = MappingProxyType(
    {description.register: description for description in _REGISTER_DESCRIPTIONS}
)


class _ReceiveMessage(Protocol):
    """Subset of an MQTT receive message used by the transport."""

    payload: str | bytes


class _MqttApi(Protocol):
    """Home Assistant MQTT API subset, injectable for isolated tests."""

    async def async_subscribe(
        self,
        hass: HomeAssistant,
        topic: str,
        msg_callback: Callable[[_ReceiveMessage], None],
        qos: int = 0,
        encoding: str | None = "utf-8",
    ) -> CALLBACK_TYPE:
        """Subscribe to a state topic."""

    async def async_publish(
        self,
        hass: HomeAssistant,
        topic: str,
        payload: H60Value,
        qos: int = 0,
        retain: bool = False,
        encoding: str | None = "utf-8",
    ) -> None:
        """Publish a command payload."""


class H60Error(Exception):
    """Base H60 transport error."""


class H60UnknownRegisterError(H60Error, KeyError):
    """Raised when a register is not part of the configured catalog."""


class H60ReadOnlyRegisterError(H60Error):
    """Raised when attempting to write a read-only register."""


class H60InvalidValueError(H60Error, ValueError):
    """Raised when a write is not a finite, accepted register value."""


class H60MqttClient:
    """Subscribe to and publish raw numeric H60 register values."""

    def __init__(
        self,
        hass: HomeAssistant,
        topic_prefix: str,
        *,
        registers: Mapping[str, H60RegisterDescription] = H60_REGISTERS,
        mqtt_api: _MqttApi | None = None,
    ) -> None:
        """Initialize an inactive H60 MQTT client."""
        normalized_prefix = topic_prefix.strip().strip("/")
        if (
            not normalized_prefix
            or "+" in normalized_prefix
            or "#" in normalized_prefix
        ):
            msg = "H60 topic prefix must be a non-empty concrete MQTT topic"
            raise ValueError(msg)

        self._hass = hass
        self._topic_prefix = normalized_prefix
        self._registers = registers
        self._mqtt_api = mqtt_api
        self._listeners: set[H60Listener] = set()
        self._unsubscribers: list[CALLBACK_TYPE] = []
        self._values: dict[str, H60Value] = {}

    @property
    def topic_prefix(self) -> str:
        """Return the normalized H60 MQTT topic prefix."""
        return self._topic_prefix

    @property
    def values(self) -> Mapping[str, H60Value]:
        """Return a read-only snapshot of received register values."""
        return MappingProxyType(self._values.copy())

    def value(self, register: str) -> H60Value | None:
        """Return the latest received value for a register, if any."""
        self._description(register)
        return self._values.get(register)

    def state_topic(self, register: str) -> str:
        """Return the exact MQTT state topic for a configured register."""
        self._description(register)
        return f"{self._topic_prefix}/HP/{register}"

    def command_topic(self, register: str) -> str:
        """Return the exact MQTT command topic for a configured register."""
        self._description(register)
        return f"{self._topic_prefix}/HP/SET/{register}"

    @callback
    def add_listener(self, listener: H60Listener) -> CALLBACK_TYPE:
        """Register a callback invoked with register and parsed numeric value."""
        self._listeners.add(listener)

        @callback
        def remove_listener() -> None:
            self._listeners.discard(listener)

        return remove_listener

    async def async_start(self) -> None:
        """Subscribe to each configured register's exact state topic."""
        if self._unsubscribers:
            return

        mqtt_api = self._get_mqtt_api()
        try:
            for register in self._registers:
                unsubscribe = await mqtt_api.async_subscribe(
                    self._hass,
                    self.state_topic(register),
                    self._message_callback(register),
                    qos=0,
                    encoding="utf-8",
                )
                self._unsubscribers.append(unsubscribe)
        except Exception:
            self._unsubscribe_all()
            raise

    async def async_stop(self) -> None:
        """Remove all MQTT subscriptions owned by this transport."""
        self._unsubscribe_all()

    async def async_set_value(self, register: str, value: H60Value) -> None:
        """Publish an unscaled numeric value to a writable H60 register."""
        description = self._description(register)
        if not description.writable:
            raise H60ReadOnlyRegisterError(register)

        normalized_value = self._validate_value(description, value)
        await self._get_mqtt_api().async_publish(
            self._hass,
            self.command_topic(register),
            normalized_value,
            qos=0,
            retain=False,
            encoding="utf-8",
        )

    def _get_mqtt_api(self) -> _MqttApi:
        """Load Home Assistant's MQTT API only when H60 support is enabled."""
        if self._mqtt_api is None:
            self._mqtt_api = importlib.import_module("homeassistant.components.mqtt")
        return self._mqtt_api

    def _description(self, register: str) -> H60RegisterDescription:
        try:
            return self._registers[register]
        except KeyError as err:
            raise H60UnknownRegisterError(register) from err

    def _message_callback(self, register: str) -> Callable[[_ReceiveMessage], None]:
        @callback
        def receive(message: _ReceiveMessage) -> None:
            self._receive_value(register, message.payload)

        return receive

    @callback
    def _receive_value(self, register: str, payload: str | bytes) -> None:
        if isinstance(payload, bytes):
            try:
                payload = payload.decode()
            except UnicodeDecodeError:
                _LOGGER.warning(
                    "Ignoring non-UTF-8 H60 payload for register %s", register
                )
                return

        try:
            numeric_value = float(payload.strip())
        except AttributeError, ValueError:
            _LOGGER.warning(
                "Ignoring non-numeric H60 payload for register %s: %r",
                register,
                payload,
            )
            return

        if not math.isfinite(numeric_value):
            _LOGGER.warning(
                "Ignoring non-finite H60 payload for register %s: %r",
                register,
                payload,
            )
            return

        value: H60Value = (
            int(numeric_value) if numeric_value.is_integer() else numeric_value
        )
        self._values[register] = value
        for listener in tuple(self._listeners):
            listener(register, value)

    @staticmethod
    def _validate_value(
        description: H60RegisterDescription, value: H60Value
    ) -> H60Value:
        if isinstance(value, bool):
            value = int(value)
        if not isinstance(value, int | float) or not math.isfinite(value):
            raise H60InvalidValueError(value)
        if (
            description.native_min_value is not None
            and value < description.native_min_value
        ):
            raise H60InvalidValueError(value)
        if (
            description.native_max_value is not None
            and value > description.native_max_value
        ):
            raise H60InvalidValueError(value)
        if description.options and value not in description.option_values:
            raise H60InvalidValueError(value)
        if description.native_step is not None:
            origin = description.native_min_value or 0
            offset = Decimal(str(value)) - Decimal(str(origin))
            steps = offset / Decimal(str(description.native_step))
            if steps != steps.to_integral_value():
                raise H60InvalidValueError(value)
        return value

    @callback
    def _unsubscribe_all(self) -> None:
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        self._unsubscribers.clear()


def enabled_h60_registers(
    descriptions: Iterable[H60RegisterDescription] = H60_REGISTERS.values(),
) -> tuple[H60RegisterDescription, ...]:
    """Return catalog entries intended to be enabled on first setup."""
    return tuple(
        description for description in descriptions if description.enabled_by_default
    )
