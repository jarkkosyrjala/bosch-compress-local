"""Normalize gateway measurements before any entity can publish them."""

from dataclasses import replace
from typing import Any

import pytest

from custom_components.bosch_compress.models import BoschEndpoint


@pytest.mark.parametrize("unit", ["C", "°C"])
@pytest.mark.parametrize("value", [-3276.8, -3200.0, 3200.0, 3276.7])
def test_temperature_sentinels_preserve_capabilities(unit: str, value: float) -> None:
    """Missing readings retain their endpoint and all writable metadata."""
    payload = {
        "id": "/example/temperature",
        "type": "floatValue",
        "value": value,
        "unitOfMeasure": unit,
        "writeable": True,
        "minValue": 5,
        "maxValue": 80,
        "allowedValues": [10, 20, 30],
    }
    endpoint = BoschEndpoint.from_payload(payload)
    assert endpoint.value is None
    assert endpoint.raw_value == value
    assert endpoint.value_error == "ems_temperature_sentinel"
    assert endpoint.path == payload["id"] and endpoint.unit == unit
    assert endpoint.writable and endpoint.value_type == "floatValue"
    assert (endpoint.minimum, endpoint.maximum) == (5, 80)
    assert endpoint.allowed_values == (10, 20, 30)
    assert payload["value"] == value  # Never mutate the input or leak raw in repr.
    assert str(value) not in repr(endpoint)


@pytest.mark.parametrize("value_type", ["floatValue", "integerValue"])
@pytest.mark.parametrize(
    ("value", "reason"),
    [
        (True, "not_numeric"),
        (False, "not_numeric"),
        ("21.5", "not_numeric"),
        ([], "not_numeric"),
        ({"value": 21}, "not_numeric"),
        (float("nan"), "not_finite"),
        (float("inf"), "not_finite"),
        (float("-inf"), "not_finite"),
    ],
)
def test_invalid_numeric_readings(value_type: str, value: Any, reason: str) -> None:
    """Invalid numeric input becomes unknown for all numeric units."""
    endpoint = BoschEndpoint("/example/value", value, value_type, False, unit="kWh")
    assert endpoint.value is None and endpoint.value_error == reason
    assert endpoint.raw_value is value


@pytest.mark.parametrize("value", [None, -40.0, -10.1, 0, 45.1, 85.0, 150.0])
def test_valid_temperatures_are_not_comfort_filtered(value: float | None) -> None:
    """Negative outdoor and hot-water values are not bounded by room limits."""
    endpoint = BoschEndpoint(
        "/example/temperature", value, "floatValue", False, unit="C"
    )
    assert endpoint.value == value and endpoint.value_error is None


@pytest.mark.parametrize("unit", [None, "kWh", "kW", "s", "%", "K"])
@pytest.mark.parametrize("value", [-3276.8, 3200, 3276.7, 65535])
def test_temperature_sentinels_do_not_apply_to_other_units(
    unit: str | None, value: float
) -> None:
    """Do not guess error encodings for counters or temperature differences."""
    endpoint = BoschEndpoint("/example/value", value, "floatValue", False, unit=unit)
    assert endpoint.value == value and endpoint.value_error is None


@pytest.mark.parametrize(
    ("value_type", "value"),
    [
        ("stringValue", "unknown"),
        ("stringValue", "on"),
        ("refEnum", [{"id": "/child"}]),
        ("errorList", [{"code": 3276}]),
        ("switchProgram", [{"temperature": -3276.8}]),
    ],
)
def test_non_numeric_payloads_are_preserved(value_type: str, value: Any) -> None:
    """Strings, references, notifications and schedules retain their semantics."""
    endpoint = BoschEndpoint("/example/value", value, value_type, False)
    assert endpoint.value is value and endpoint.value_error is None


def test_replacement_clears_invalid_value_and_reason() -> None:
    """Recovery uses the fresh reading without carrying an old error forward."""
    missing = BoschEndpoint("/temperature", -3276.8, "floatValue", True, unit="C")
    recovered = replace(missing, value=21.5)
    assert recovered.value == recovered.raw_value == 21.5
    assert recovered.value_error is None and recovered.writable
