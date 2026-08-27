"""Tests for the optional H60 MQTT supplemental transport."""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast

import pytest

from custom_components.bosch_compress.h60 import (
    H60_REGISTERS,
    H60InvalidValueError,
    H60MqttClient,
    H60ReadOnlyRegisterError,
    H60RegisterKind,
    H60UnknownRegisterError,
    enabled_h60_registers,
)


@dataclass
class _Message:
    payload: str | bytes


class _FakeMqttApi:
    def __init__(self) -> None:
        self.subscriptions: dict[str, Callable[[_Message], None]] = {}
        self.unsubscribed: list[str] = []
        self.published: list[tuple[str, int | float, int, bool, str | None]] = []

    async def async_subscribe(
        self,
        hass: Any,
        topic: str,
        msg_callback: Callable[[_Message], None],
        qos: int = 0,
        encoding: str | None = "utf-8",
    ) -> Callable[[], None]:
        del hass
        assert qos == 0
        assert encoding == "utf-8"
        self.subscriptions[topic] = msg_callback

        def unsubscribe() -> None:
            self.unsubscribed.append(topic)

        return unsubscribe

    async def async_publish(
        self,
        hass: Any,
        topic: str,
        payload: int | float,
        qos: int = 0,
        retain: bool = False,
        encoding: str | None = "utf-8",
    ) -> None:
        del hass
        self.published.append((topic, payload, qos, retain, encoding))

    def emit(self, topic: str, payload: str | bytes) -> None:
        self.subscriptions[topic](_Message(payload))


def _client(fake_mqtt: _FakeMqttApi) -> H60MqttClient:
    return H60MqttClient(
        cast(Any, object()),
        "/gateway-prefix/",
        mqtt_api=cast(Any, fake_mqtt),
    )


def test_register_catalog_preserves_confirmed_semantics() -> None:
    """The catalog models register meanings rather than fake climates."""
    room_target = H60_REGISTERS["0203"]
    assert room_target.kind is H60RegisterKind.NUMBER
    assert (
        room_target.native_min_value,
        room_target.native_max_value,
        room_target.native_step,
    ) == (13, 29, 0.5)

    assert H60_REGISTERS["1231"].kind is H60RegisterKind.SWITCH
    assert H60_REGISTERS["1231"].option_values == ()

    dhw_program = H60_REGISTERS["2213"]
    assert dhw_program.kind is H60RegisterKind.SELECT
    assert dhw_program.options == (
        (0, "Off"),
        (1, "Eco / Normal"),
        (2, "Comfort"),
        (4, "Manual time program"),
        (5, "Eco+"),
    )
    assert 3 not in dhw_program.option_values

    assert H60_REGISTERS["1A20"].key == "active_alarm"
    assert H60_REGISTERS["2A91"].key == "alarm_code"
    assert H60_REGISTERS["9108"].unit == "kW"


def test_catalog_covers_enabled_legacy_entities_without_bosch_duplicates() -> None:
    """Keep enabled legacy gaps while omitting confirmed Bosch replacements."""
    expected_enabled = {
        "0005",
        "0006",
        "000A",
        "000B",
        "000C",
        "000E",
        "0011",
        "0203",
        "0205",
        "0206",
        "1231",
        "1A04",
        "1A05",
        "1A06",
        "1A07",
        "1A0C",
        "1A20",
        "2201",
        "2202",
        "2204",
        "2213",
        "2215",
        "2227",
        "2228",
        "2229",
        "2233",
        "2234",
        "2A91",
        "2C61",
        "3104",
        "3109",
        "3110",
        "3120",
        "3128",
        "5C51",
        "5C52",
        "5C53",
        "5C54",
        "5C55",
        "5C56",
        "5C57",
        "5C58",
        "5C59",
        "6C50",
        "6C60",
        "9108",
    }
    assert {item.register for item in enabled_h60_registers()} == expected_enabled

    confirmed_bosch_replacements = {
        "0002",
        "0003",
        "0004",
        "0007",
        "0008",
        "0009",
        "0020",
        "0021",
        "0107",
        "0111",
        "1A01",
        "3108",
    }
    assert confirmed_bosch_replacements.isdisjoint(H60_REGISTERS)
    assert not H60_REGISTERS["020C"].enabled_by_default
    assert not H60_REGISTERS["0220"].enabled_by_default


@pytest.mark.parametrize(
    ("register", "minimum", "maximum", "step"),
    [
        ("0203", 13, 29, 0.5),
        ("0205", 6, 30, 0.5),
        ("0206", 5, 25, 0.5),
        ("2201", 0, 1, 1),
        ("2202", 0, 2, 1),
        ("2204", 0, 10, 1),
    ],
)
def test_fake_climate_numbers_keep_discovery_ranges(
    register: str, minimum: float, maximum: float, step: float
) -> None:
    """Legacy fake climates become bounded number entities."""
    description = H60_REGISTERS[register]
    assert description.kind is H60RegisterKind.NUMBER
    assert description.writable
    assert (
        description.native_min_value,
        description.native_max_value,
        description.native_step,
    ) == (minimum, maximum, step)
    assert not description.options


@pytest.mark.parametrize("register", ["1231", "2215", "2233", "2234"])
def test_fake_binary_climates_become_switches(register: str) -> None:
    """Binary legacy controls use switches with exact 0/1 bounds."""
    description = H60_REGISTERS[register]
    assert description.kind is H60RegisterKind.SWITCH
    assert description.writable
    assert (
        description.native_min_value,
        description.native_max_value,
        description.native_step,
    ) == (0, 1, 1)


def test_subscribes_exact_topics_and_notifies_numeric_values() -> None:
    """State payloads are parsed without register-specific scaling."""
    fake_mqtt = _FakeMqttApi()
    client = _client(fake_mqtt)
    received: list[tuple[str, int | float]] = []
    client.add_listener(lambda register, value: received.append((register, value)))

    asyncio.run(client.async_start())

    assert len(fake_mqtt.subscriptions) == len(H60_REGISTERS)
    assert "gateway-prefix/HP/0203" in fake_mqtt.subscriptions
    assert all(
        "+" not in topic and "#" not in topic for topic in fake_mqtt.subscriptions
    )

    fake_mqtt.emit("gateway-prefix/HP/0203", "22.50")
    fake_mqtt.emit("gateway-prefix/HP/1A20", b"1")

    assert received == [("0203", 22.5), ("1A20", 1)]
    assert client.value("0203") == 22.5
    assert client.values["1A20"] == 1


def test_invalid_payload_does_not_replace_latest_value() -> None:
    """Malformed or non-finite state payloads cannot corrupt entity state."""
    fake_mqtt = _FakeMqttApi()
    client = _client(fake_mqtt)
    received: list[int | float] = []
    client.add_listener(lambda _register, value: received.append(value))
    asyncio.run(client.async_start())

    topic = "gateway-prefix/HP/1231"
    fake_mqtt.emit(topic, "0")
    fake_mqtt.emit(topic, "not-a-number")
    fake_mqtt.emit(topic, "nan")
    fake_mqtt.emit(topic, b"\xff")

    assert client.value("1231") == 0
    assert received == [0]


def test_publishes_existing_automation_values_unchanged() -> None:
    """Current numeric automation behavior reaches the exact SET topics."""
    fake_mqtt = _FakeMqttApi()
    client = _client(fake_mqtt)

    asyncio.run(client.async_set_value("0203", 22.5))
    asyncio.run(client.async_set_value("1231", 1))
    asyncio.run(client.async_set_value("2213", 5))

    assert fake_mqtt.published == [
        ("gateway-prefix/HP/SET/0203", 22.5, 0, False, "utf-8"),
        ("gateway-prefix/HP/SET/1231", 1, 0, False, "utf-8"),
        ("gateway-prefix/HP/SET/2213", 5, 0, False, "utf-8"),
    ]


def test_rejects_unsafe_or_unknown_writes() -> None:
    """Only finite values accepted by writable descriptors are published."""
    fake_mqtt = _FakeMqttApi()
    client = _client(fake_mqtt)

    with pytest.raises(H60InvalidValueError):
        asyncio.run(client.async_set_value("0203", 30))
    with pytest.raises(H60InvalidValueError):
        asyncio.run(client.async_set_value("0203", 22.3))
    with pytest.raises(H60InvalidValueError):
        asyncio.run(client.async_set_value("2213", 3))
    with pytest.raises(H60InvalidValueError):
        asyncio.run(client.async_set_value("1231", float("inf")))
    with pytest.raises(H60ReadOnlyRegisterError):
        asyncio.run(client.async_set_value("1A20", 0))
    with pytest.raises(H60UnknownRegisterError):
        asyncio.run(client.async_set_value("FFFF", 1))

    assert fake_mqtt.published == []


def test_stop_unsubscribes_every_register() -> None:
    """Unload removes all subscriptions and can be called repeatedly."""
    fake_mqtt = _FakeMqttApi()
    client = _client(fake_mqtt)
    asyncio.run(client.async_start())

    asyncio.run(client.async_stop())
    asyncio.run(client.async_stop())

    assert set(fake_mqtt.unsubscribed) == set(fake_mqtt.subscriptions)
    assert len(fake_mqtt.unsubscribed) == len(H60_REGISTERS)


@pytest.mark.parametrize("prefix", ["", "/", "gateway/+", "gateway/#"])
def test_rejects_invalid_topic_prefix(prefix: str) -> None:
    """A configured prefix must resolve to concrete MQTT topics."""
    with pytest.raises(ValueError, match="topic prefix"):
        H60MqttClient(cast(Any, object()), prefix, mqtt_api=cast(Any, object()))
