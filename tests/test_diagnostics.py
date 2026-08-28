"""Tests for sanitized Bosch Compress diagnostics."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any, cast

from custom_components.bosch_compress import diagnostics


def test_diagnostics_report_only_local_runtime_metadata(monkeypatch: Any) -> None:
    """Diagnostics contain discovery health without retired transport state."""
    endpoints = {
        "/readable": SimpleNamespace(writable=False, value_type="floatValue"),
        "/writable": SimpleNamespace(writable=True, value_type="stringValue"),
    }
    coordinator = SimpleNamespace(
        discovered_endpoints=endpoints,
        endpoint_errors={"/failed": TimeoutError()},
        last_update_success=True,
    )
    entry = SimpleNamespace(
        entry_id="test-entry",
        version=2,
        state="loaded",
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

    assert result["entry"] == {"version": 2, "state": "loaded"}
    assert result["discovery"] == {
        "endpoint_count": 2,
        "writable_endpoint_count": 1,
        "endpoint_types": ["floatValue", "stringValue"],
        "endpoint_paths": ["/readable", "/writable"],
    }
    assert result["polling"]["endpoint_errors"] == {"/failed": "TimeoutError"}
    assert result["entities"] == {"count": 2, "disabled_count": 1}
    assert "h60" not in json.dumps(result).casefold()
