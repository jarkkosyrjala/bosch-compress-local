"""Flow-level tests for Bosch Compress configuration."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD
from homeassistant.data_entry_flow import AbortFlow, FlowResultType

import custom_components.bosch_compress.config_flow as config_flow_module
from custom_components.bosch_compress.api import (
    BoschAuthenticationError,
    BoschConnectionError,
    BoschResponseError,
)
from custom_components.bosch_compress.config_flow import (
    BoschCompressConfigFlow,
    BoschCompressOptionsFlow,
    _normalize_host,
)
from custom_components.bosch_compress.const import CONF_ACCESS_TOKEN

_INTEGRATION_PATH = Path(__file__).parents[1] / "custom_components" / "bosch_compress"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (" 192.0.2.1 ", "192.0.2.1"),
        ("http://gateway.local/", "gateway.local"),
        ("HTTPS://gateway.local:8080///", "gateway.local:8080"),
        ("2001:db8::1", "[2001:db8::1]"),
        ("http://[2001:0db8::1]/", "[2001:db8::1]"),
        ("[2001:db8::1]:8080", "[2001:db8::1]:8080"),
    ],
)
def test_normalize_host_accepts_safe_local_addresses(value: str, expected: str) -> None:
    """Host normalization accepts hostnames, IPv4, and URL-safe IPv6."""
    assert _normalize_host(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "",
        "http://",
        "user@gateway.local",
        "http://user@gateway.local",
        "gateway.local/path",
        "gateway.local?query=yes",
        "gateway.local#fragment",
        "gateway local",
        "2001:db8::not-an-address",
        "[2001:db8::1",
        "gateway.local:not-a-port",
        "gateway.local:",
        "[2001:db8::1]:",
    ],
)
def test_normalize_host_rejects_unsafe_or_invalid_authorities(value: str) -> None:
    """Paths, userinfo, malformed ports, and malformed IPv6 are rejected."""
    with pytest.raises(ValueError):
        _normalize_host(value)


def _new_user_flow() -> BoschCompressConfigFlow:
    """Return a flow whose HA-dependent identity hooks are observable mocks."""
    flow = BoschCompressConfigFlow()
    flow.hass = cast(Any, object())
    flow.async_set_unique_id = AsyncMock()  # type: ignore[method-assign]
    flow._abort_if_unique_id_configured = Mock()  # type: ignore[method-assign]
    return flow


def test_user_flow_normalizes_validates_and_creates_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A valid setup stores the normalized local address and stable identity."""
    validate = AsyncMock(return_value="gateway-uuid")
    monkeypatch.setattr(config_flow_module, "_async_validate_input", validate)
    flow = _new_user_flow()
    user_input = {
        CONF_HOST: " http://gateway.local/ ",
        CONF_ACCESS_TOKEN: "login",
        CONF_PASSWORD: "password",
    }

    result = asyncio.run(flow.async_step_user(user_input))

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {**user_input, CONF_HOST: "gateway.local"}
    validate.assert_awaited_once_with(flow.hass, result["data"])
    cast(AsyncMock, flow.async_set_unique_id).assert_awaited_once_with("gateway-uuid")
    cast(Mock, flow._abort_if_unique_id_configured).assert_called_once_with(
        updates={CONF_HOST: "gateway.local"}
    )


@pytest.mark.parametrize(
    ("error", "translation_key"),
    [
        (BoschAuthenticationError("bad credentials"), "invalid_auth"),
        (BoschConnectionError("offline"), "cannot_connect"),
        (BoschResponseError("bad response"), "unknown"),
    ],
)
def test_user_flow_maps_validation_failures(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    translation_key: str,
) -> None:
    """Expected validation failures remain on the form with translated keys."""
    monkeypatch.setattr(
        config_flow_module,
        "_async_validate_input",
        AsyncMock(side_effect=error),
    )
    flow = _new_user_flow()

    result = asyncio.run(
        flow.async_step_user(
            {
                CONF_HOST: "gateway.local",
                CONF_ACCESS_TOKEN: "login",
                CONF_PASSWORD: "password",
            }
        )
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": translation_key}


def test_user_flow_rejects_invalid_host_without_network_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unsafe host input is rejected before constructing a gateway request."""
    validate = AsyncMock()
    monkeypatch.setattr(config_flow_module, "_async_validate_input", validate)
    flow = _new_user_flow()

    result = asyncio.run(
        flow.async_step_user(
            {
                CONF_HOST: "user@gateway.local",
                CONF_ACCESS_TOKEN: "login",
                CONF_PASSWORD: "password",
            }
        )
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_input"}
    validate.assert_not_awaited()


def test_user_flow_aborts_when_gateway_identity_is_already_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A duplicate stable gateway identity is never created a second time."""
    monkeypatch.setattr(
        config_flow_module,
        "_async_validate_input",
        AsyncMock(return_value="gateway-uuid"),
    )
    flow = _new_user_flow()
    flow._abort_if_unique_id_configured = Mock(  # type: ignore[method-assign]
        side_effect=AbortFlow("already_configured")
    )

    with pytest.raises(AbortFlow, match="already_configured"):
        asyncio.run(
            flow.async_step_user(
                {
                    CONF_HOST: "gateway.local",
                    CONF_ACCESS_TOKEN: "login",
                    CONF_PASSWORD: "password",
                }
            )
        )


class _ConfigEntries:
    """Provide the config-entry lookup used by an options flow."""

    def __init__(self, entry: Any) -> None:
        self.entry = entry

    def async_get_known_entry(self, entry_id: str) -> Any:
        """Return the requested config entry."""
        assert entry_id == self.entry.entry_id
        return self.entry


def _new_options_flow() -> tuple[BoschCompressOptionsFlow, Any]:
    """Return an initialized options flow and its fake config entry."""
    entry = SimpleNamespace(
        entry_id="entry-id",
        unique_id="gateway-uuid",
        data={
            CONF_HOST: "gateway.local",
            CONF_ACCESS_TOKEN: "login",
            CONF_PASSWORD: "password",
        },
        options={},
    )
    flow = BoschCompressOptionsFlow()
    flow.handler = entry.entry_id
    flow.hass = cast(
        Any,
        SimpleNamespace(config_entries=_ConfigEntries(entry)),
    )
    return flow, entry


def test_options_flow_validates_gateway_before_saving_normalized_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Options save only after the new address resolves to the same gateway."""
    validate = AsyncMock(return_value="gateway-uuid")
    monkeypatch.setattr(config_flow_module, "_async_validate_input", validate)
    flow, entry = _new_options_flow()

    result = asyncio.run(flow.async_step_init({CONF_HOST: "http://[2001:db8::1]/"}))

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_HOST: "[2001:db8::1]"}
    validate.assert_awaited_once_with(
        flow.hass,
        {**entry.data, CONF_HOST: "[2001:db8::1]"},
    )


def test_options_flow_rejects_a_different_gateway(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Changing the address cannot silently move an entry to other hardware."""
    monkeypatch.setattr(
        config_flow_module,
        "_async_validate_input",
        AsyncMock(return_value="other-gateway"),
    )
    flow, _entry = _new_options_flow()

    result = asyncio.run(flow.async_step_init({CONF_HOST: "other.local"}))

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "wrong_gateway"}


@pytest.mark.parametrize(
    ("error", "translation_key"),
    [
        (BoschAuthenticationError("bad credentials"), "invalid_auth"),
        (BoschConnectionError("offline"), "cannot_connect"),
        (BoschResponseError("bad response"), "unknown"),
    ],
)
def test_options_flow_maps_validation_failures(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    translation_key: str,
) -> None:
    """Options validation emits keys from the options translation namespace."""
    monkeypatch.setattr(
        config_flow_module,
        "_async_validate_input",
        AsyncMock(side_effect=error),
    )
    flow, _entry = _new_options_flow()

    result = asyncio.run(flow.async_step_init({CONF_HOST: "gateway.local"}))

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": translation_key}


def test_reauth_validates_identity_before_updating_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reauthentication verifies identity and updates only secret fields."""
    validate = AsyncMock(return_value="gateway-uuid")
    monkeypatch.setattr(config_flow_module, "_async_validate_input", validate)
    entry = SimpleNamespace(
        data={
            CONF_HOST: "gateway.local",
            CONF_ACCESS_TOKEN: "old-login",
            CONF_PASSWORD: "old-password",
        }
    )
    flow = BoschCompressConfigFlow()
    flow.hass = cast(Any, object())
    flow._get_reauth_entry = Mock(return_value=entry)  # type: ignore[method-assign]
    flow.async_set_unique_id = AsyncMock()  # type: ignore[method-assign]
    flow._abort_if_unique_id_mismatch = Mock()  # type: ignore[method-assign]
    expected_result = cast(Any, {"type": FlowResultType.ABORT})
    flow.async_update_reload_and_abort = Mock(  # type: ignore[method-assign]
        return_value=expected_result
    )

    result = asyncio.run(
        flow.async_step_reauth_confirm(
            {CONF_ACCESS_TOKEN: "new-login", CONF_PASSWORD: "new-password"}
        )
    )

    assert result is expected_result
    validate.assert_awaited_once_with(
        flow.hass,
        {
            CONF_HOST: "gateway.local",
            CONF_ACCESS_TOKEN: "new-login",
            CONF_PASSWORD: "new-password",
        },
    )
    cast(Mock, flow._abort_if_unique_id_mismatch).assert_called_once_with()
    cast(Mock, flow.async_update_reload_and_abort).assert_called_once_with(
        entry,
        data_updates={
            CONF_ACCESS_TOKEN: "new-login",
            CONF_PASSWORD: "new-password",
        },
    )


def test_reauth_aborts_before_update_when_gateway_identity_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Credentials for another gateway trigger the translated mismatch abort."""
    monkeypatch.setattr(
        config_flow_module,
        "_async_validate_input",
        AsyncMock(return_value="other-gateway"),
    )
    entry = SimpleNamespace(
        data={
            CONF_HOST: "gateway.local",
            CONF_ACCESS_TOKEN: "old-login",
            CONF_PASSWORD: "old-password",
        }
    )
    flow = BoschCompressConfigFlow()
    flow.hass = cast(Any, object())
    flow._get_reauth_entry = Mock(return_value=entry)  # type: ignore[method-assign]
    flow.async_set_unique_id = AsyncMock()  # type: ignore[method-assign]
    flow._abort_if_unique_id_mismatch = Mock(  # type: ignore[method-assign]
        side_effect=AbortFlow("unique_id_mismatch")
    )
    update = Mock()
    flow.async_update_reload_and_abort = update  # type: ignore[method-assign]

    with pytest.raises(AbortFlow, match="unique_id_mismatch"):
        asyncio.run(
            flow.async_step_reauth_confirm(
                {CONF_ACCESS_TOKEN: "new-login", CONF_PASSWORD: "new-password"}
            )
        )

    update.assert_not_called()


def test_config_flow_factory_returns_current_options_flow() -> None:
    """The config flow exposes its current options-flow implementation."""
    entry = cast(ConfigEntry[Any], object())
    assert isinstance(
        BoschCompressConfigFlow.async_get_options_flow(entry),
        BoschCompressOptionsFlow,
    )


def test_config_and_options_error_translations_are_complete() -> None:
    """Every emitted flow key exists in both canonical English resources."""
    strings = json.loads((_INTEGRATION_PATH / "strings.json").read_text())
    translation = json.loads(
        (_INTEGRATION_PATH / "translations" / "en.json").read_text()
    )

    assert strings == translation
    assert "unique_id_mismatch" in strings["config"]["abort"]
    expected_errors = {
        "cannot_connect",
        "invalid_input",
        "invalid_auth",
        "unknown",
        "wrong_gateway",
    }
    assert expected_errors <= strings["config"]["error"].keys()
    assert expected_errors <= strings["options"]["error"].keys()
