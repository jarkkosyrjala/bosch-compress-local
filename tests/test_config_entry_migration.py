"""Tests for the Bosch Compress config-entry schema."""

from __future__ import annotations

import pytest
import voluptuous as vol
from homeassistant.const import CONF_HOST, CONF_PASSWORD

from custom_components.bosch_compress.config_flow import (
    BoschCompressConfigFlow,
    _user_schema,
)
from custom_components.bosch_compress.const import (
    CONF_ACCESS_TOKEN,
    CONFIG_ENTRY_VERSION,
)


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
        schema({**values, "unexpected_transport_option": "not-supported"})
    assert BoschCompressConfigFlow.VERSION == CONFIG_ENTRY_VERSION
