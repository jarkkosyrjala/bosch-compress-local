"""Typed models for the Bosch local gateway API."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from math import isfinite
from typing import Any, Self

# EMS reserves 16-bit magnitudes >= 0x7D00 for missing readings, including
# 0x8000 (-32768). The gateway has already converted tenths to degrees Celsius.
# https://github.com/emsesp/EMS-ESP32/blob/f20d9391754ca2578f703cee39dfaa8c4a041545/src/core/helpers.cpp#L638-L644
_EMS_TEMPERATURE_NOT_SET = 0x7D00 / 10
NUMERIC_VALUE_TYPES = frozenset({"floatValue", "integerValue"})


@dataclass(frozen=True, slots=True)
class BoschEndpoint:
    """A typed snapshot of one Bosch gateway endpoint."""

    path: str
    value: Any
    value_type: str
    writable: bool
    minimum: int | float | None = None
    maximum: int | float | None = None
    allowed_values: tuple[Any, ...] = ()
    unit: str | None = None
    raw_value: Any = field(init=False, repr=False, compare=False)
    value_error: str | None = field(init=False, default=None)

    def __post_init__(self) -> None:
        """Normalize missing measurements without discarding endpoint metadata."""
        value = self.value
        object.__setattr__(self, "raw_value", value)
        if self.value_type not in NUMERIC_VALUE_TYPES or value is None:
            return
        if isinstance(value, bool) or not isinstance(value, int | float):
            reason = "not_numeric"
        elif isinstance(value, float) and not isfinite(value):
            reason = "not_finite"
        elif self.unit in {"C", "°C"} and abs(value) >= _EMS_TEMPERATURE_NOT_SET:
            reason = "ems_temperature_sentinel"
        else:
            return
        object.__setattr__(self, "value", None)
        object.__setattr__(self, "value_error", reason)

    @property
    def is_reference(self) -> bool:
        """Return whether this endpoint contains references to child endpoints."""
        return self.value_type == "refEnum"

    @classmethod
    def from_payload(
        cls,
        payload: Mapping[str, Any],
        *,
        path: str | None = None,
    ) -> Self:
        """Build an endpoint from a decrypted Bosch response payload."""
        endpoint_path = path if path is not None else payload.get("id")
        value_type = payload.get("type")
        if not isinstance(endpoint_path, str) or not endpoint_path.startswith("/"):
            raise ValueError("Bosch endpoint payload has no valid path")
        if not isinstance(value_type, str) or not value_type:
            raise ValueError("Bosch endpoint payload has no valid type")

        allowed_values = payload.get("allowedValues", ())
        if not isinstance(allowed_values, (list, tuple)):
            allowed_values = ()

        minimum = payload.get("minValue")
        maximum = payload.get("maxValue")
        unit = payload.get("unitOfMeasure")
        if "value" in payload:
            value = payload["value"]
        elif "values" in payload:
            value = payload["values"]
        elif "switchPoints" in payload:
            value = payload["switchPoints"]
        else:
            value = None

        return cls(
            path=endpoint_path,
            value=value,
            value_type=value_type,
            writable=bool(payload.get("writeable", payload.get("writable", False))),
            minimum=minimum if isinstance(minimum, (int, float)) else None,
            maximum=maximum if isinstance(maximum, (int, float)) else None,
            allowed_values=tuple(allowed_values),
            unit=unit if isinstance(unit, str) else None,
        )
