"""Tests for sanitized Bosch Compress diagnostics."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any, cast

from custom_components.bosch_compress import diagnostics
from custom_components.bosch_compress.models import BoschEndpoint


def test_diagnostics_report_only_local_runtime_metadata(monkeypatch: Any) -> None:
    """Diagnostics contain discovery health without retired transport state."""
    endpoints = {
        "/readable": BoschEndpoint("/readable", 21.0, "floatValue", False, unit="C"),
        "/writable": BoschEndpoint("/writable", "private-value", "stringValue", True),
    }
    coordinator = SimpleNamespace(
        discovered_endpoints=endpoints,
        data={
            **endpoints,
            "/readable": BoschEndpoint(
                "/readable", -3276.8, "floatValue", False, unit="C"
            ),
        },
        endpoint_errors={"/failed": TimeoutError()},
        last_update_success=True,
    )
    entry = SimpleNamespace(
        entry_id="test-entry",
        version=2,
        runtime_data=SimpleNamespace(coordinator=coordinator),
    )
    registry_entries = [
        SimpleNamespace(disabled_by=None),
        SimpleNamespace(disabled_by="integration"),
    ]
    monkeypatch.setattr(diagnostics.er, "async_get", lambda _hass: object())
    monkeypatch.setattr(
        diagnostics.er,
        "async_entries_for_config_entry",
        lambda _registry, _entry_id: registry_entries,
    )

    result = asyncio.run(
        diagnostics.async_get_config_entry_diagnostics(
            cast(Any, object()), cast(Any, entry)
        )
    )

    assert result["entry"] == {"version": 2}
    assert result["discovery"] == {
        "endpoint_count": 2,
        "writable_endpoint_count": 1,
        "endpoint_types": ["floatValue", "stringValue"],
        "endpoint_paths": ["/readable", "/writable"],
    }
    assert result["polling"]["endpoint_errors"] == {"/failed": "TimeoutError"}
    assert result["polling"]["invalid_values"] == {
        "/readable": "ems_temperature_sentinel"
    }
    assert result["entities"] == {"count": 2, "disabled_count": 1}
    assert "3276" not in json.dumps(result)
    assert "private-value" not in json.dumps(result)
