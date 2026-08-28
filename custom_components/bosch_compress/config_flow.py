"""Config flow for the Bosch Compress integration."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from ipaddress import IPv6Address
from typing import Any, override
from urllib.parse import urlsplit

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST, CONF_PASSWORD
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    BoschAuthenticationError,
    BoschClient,
    BoschConnectionError,
    BoschResponseError,
)
from .const import (
    CONF_ACCESS_TOKEN,
    CONFIG_ENTRY_VERSION,
    DEFAULT_NAME,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


def _normalize_host(host: str) -> str:
    """Normalize a bare IP-module address without accepting URL paths."""
    normalized = host.strip()
    for scheme in ("http://", "https://"):
        if normalized.casefold().startswith(scheme):
            normalized = normalized[len(scheme) :]
            break
    normalized = normalized.rstrip("/")
    if (
        not normalized
        or any(character.isspace() for character in normalized)
        or any(character in normalized for character in "/#?@\\")
    ):
        raise ValueError("Host must be an address without a path")

    if normalized.count(":") > 1 and not normalized.startswith("["):
        try:
            address = IPv6Address(normalized)
        except ValueError as error:
            raise ValueError("Host must be a valid address") from error
        return f"[{address.compressed}]"
    if normalized.endswith(":"):
        raise ValueError("Host must include a port after ':'")

    try:
        parsed = urlsplit(f"//{normalized}")
        port = parsed.port
    except ValueError as error:
        raise ValueError("Host must be a valid address") from error
    if parsed.hostname is None or parsed.path or parsed.username or parsed.password:
        raise ValueError("Host must be a valid address")
    if normalized.startswith("["):
        try:
            address = IPv6Address(parsed.hostname)
        except ValueError as error:
            raise ValueError("Host must be a valid IPv6 address") from error
        return f"[{address.compressed}]" + (f":{port}" if port is not None else "")
    return normalized


def _user_schema(defaults: Mapping[str, Any] | None = None) -> vol.Schema:
    """Build the user configuration schema."""
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, "")): cv.string,
            vol.Required(
                CONF_ACCESS_TOKEN,
                default=defaults.get(CONF_ACCESS_TOKEN, ""),
            ): cv.string,
            vol.Required(CONF_PASSWORD): cv.string,
        }
    )


async def _async_validate_input(hass: HomeAssistant, data: Mapping[str, Any]) -> str:
    """Validate Bosch credentials and return the stable gateway UUID."""
    client = BoschClient(
        async_get_clientsession(hass),
        data[CONF_HOST],
        data[CONF_ACCESS_TOKEN],
        data[CONF_PASSWORD],
    )
    return await client.async_gateway_uuid()


class BoschCompressConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a Bosch Compress config flow."""

    VERSION = CONFIG_ENTRY_VERSION

    @staticmethod
    @callback
    @override
    def async_get_options_flow(config_entry: ConfigEntry[Any]) -> OptionsFlow:
        """Return the options flow handler."""
        del config_entry
        return BoschCompressOptionsFlow()

    @override
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle initial setup."""
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input = dict(user_input)
            try:
                user_input[CONF_HOST] = _normalize_host(user_input[CONF_HOST])
                gateway_uuid = await _async_validate_input(self.hass, user_input)
            except ValueError:
                errors["base"] = "invalid_input"
            except BoschAuthenticationError:
                errors["base"] = "invalid_auth"
            except BoschConnectionError:
                errors["base"] = "cannot_connect"
            except BoschResponseError:
                errors["base"] = "unknown"
            except Exception:
                _LOGGER.exception("Unexpected exception validating Bosch gateway")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(gateway_uuid)
                self._abort_if_unique_id_configured(
                    updates={CONF_HOST: user_input[CONF_HOST]}
                )
                return self.async_create_entry(title=DEFAULT_NAME, data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=_user_schema(user_input),
            errors=errors,
        )

    @override
    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start reauthentication for an existing entry."""
        del entry_data
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Validate replacement credentials."""
        errors: dict[str, str] = {}
        reauth_entry = self._get_reauth_entry()
        if user_input is not None:
            updated = {
                **reauth_entry.data,
                CONF_ACCESS_TOKEN: user_input[CONF_ACCESS_TOKEN],
                CONF_PASSWORD: user_input[CONF_PASSWORD],
            }
            try:
                gateway_uuid = await _async_validate_input(self.hass, updated)
            except BoschAuthenticationError:
                errors["base"] = "invalid_auth"
            except BoschConnectionError:
                errors["base"] = "cannot_connect"
            except BoschResponseError:
                errors["base"] = "unknown"
            except Exception:
                _LOGGER.exception("Unexpected exception reauthenticating Bosch gateway")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(gateway_uuid)
                self._abort_if_unique_id_mismatch()
                return self.async_update_reload_and_abort(
                    reauth_entry,
                    data_updates={
                        CONF_ACCESS_TOKEN: user_input[CONF_ACCESS_TOKEN],
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                    },
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ACCESS_TOKEN): cv.string,
                    vol.Required(CONF_PASSWORD): cv.string,
                }
            ),
            errors=errors,
        )


class BoschCompressOptionsFlow(OptionsFlow):
    """Handle editable local connection options."""

    @override
    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Validate and edit non-secret runtime options."""
        config_entry = self.config_entry
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                values = {CONF_HOST: _normalize_host(user_input[CONF_HOST])}
                gateway_uuid = await _async_validate_input(
                    self.hass,
                    {**config_entry.data, CONF_HOST: values[CONF_HOST]},
                )
            except ValueError:
                errors["base"] = "invalid_input"
            except BoschAuthenticationError:
                errors["base"] = "invalid_auth"
            except BoschConnectionError:
                errors["base"] = "cannot_connect"
            except BoschResponseError:
                errors["base"] = "unknown"
            except Exception:
                _LOGGER.exception("Unexpected exception validating Bosch options")
                errors["base"] = "unknown"
            else:
                if gateway_uuid != config_entry.unique_id:
                    errors["base"] = "wrong_gateway"
                else:
                    return self.async_create_entry(data=values)

        default_host = config_entry.options.get(CONF_HOST, config_entry.data[CONF_HOST])
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST, default=default_host): cv.string,
                }
            ),
            errors=errors,
        )
