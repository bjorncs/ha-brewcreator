"""Config flow for the BrewCreator integration."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import BrewCreatorAPI, BrewCreatorInvalidCredentialsError
from .const import DOMAIN
from .token_store import BrewCreatorTokenStore

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


class BrewCreatorConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for BrewCreator."""

    VERSION = 2

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                await BrewCreatorAPI(
                    user_input[CONF_USERNAME],
                    user_input[CONF_PASSWORD],
                    BrewCreatorTokenStore(self.hass),
                    async_get_clientsession(self.hass),
                ).verify_username_and_password()
            except aiohttp.ClientConnectionError:
                errors["base"] = "cannot_connect"
            except BrewCreatorInvalidCredentialsError:
                errors["base"] = "invalid_auth"
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(title="BrewCreator", data=user_input)

        return self.async_show_form(step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors)
