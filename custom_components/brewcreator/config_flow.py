"""Config flow for Ferminator Connect integration."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    BrewCreatorAPI,
    BrewCreatorInvalidCredentialsError,
    FermentationType,
    Ferminator,
)
from .const import (
    CONF_BATCH_INFO_BEER_STYLE,
    CONF_BATCH_INFO_BREW_NAME,
    CONF_BATCH_INFO_EBC,
    CONF_BATCH_INFO_FERMENTATION_TYPE,
    CONF_BATCH_INFO_FG,
    CONF_BATCH_INFO_IBU,
    CONF_BATCH_INFO_OG,
    CONF_BATCH_INFO_OWNER,
    CONF_BATCH_INFO_STARTED,
    CONF_BATCH_INFO_VOLUME,
    DOMAIN,
)
from .coordinator import BrewCreatorDataUpdateCoordinator
from .token_store import BrewCreatorTokenStore

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate the user input allows us to connect.

    Data has the keys from STEP_USER_DATA_SCHEMA with values provided by the user.
    """
    api = BrewCreatorAPI(
        data[CONF_USERNAME],
        data[CONF_PASSWORD],
        BrewCreatorTokenStore(hass),
        async_get_clientsession(hass),
    )

    await api.verify_username_and_password()

    return {"title": "BrewCreator"}


class BrewCreatorConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Ferminator Connect."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                info = await validate_input(self.hass, user_input)
            except aiohttp.ClientConnectionError:
                errors["base"] = "cannot_connect"
            except BrewCreatorInvalidCredentialsError:
                errors["base"] = "invalid_auth"
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(title=info["title"], data=user_input)

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry[BrewCreatorDataUpdateCoordinator],
    ) -> BrewCreatorOptionsFlow:
        return BrewCreatorOptionsFlow(config_entry)


class BrewCreatorOptionsFlow(OptionsFlow):
    def __init__(self, config_entry: ConfigEntry[BrewCreatorDataUpdateCoordinator]):
        self.coordinator: BrewCreatorDataUpdateCoordinator = config_entry.runtime_data

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        ferminator = next(
            (e for e in self.coordinator.data.values() if isinstance(e, Ferminator)),
            None,
        )
        if ferminator is None:
            return self.async_abort(reason="no_ferminator")
        if user_input is not None:
            await ferminator.set_batch_info(
                brew_name=user_input[CONF_BATCH_INFO_BREW_NAME],
                owner=user_input[CONF_BATCH_INFO_OWNER],
                ebc=user_input[CONF_BATCH_INFO_EBC],
                ibu=user_input[CONF_BATCH_INFO_IBU],
                volume=user_input[CONF_BATCH_INFO_VOLUME],
                fermentation_type=FermentationType(
                    user_input[CONF_BATCH_INFO_FERMENTATION_TYPE]
                ),
                og=user_input[CONF_BATCH_INFO_OG],
                fg=user_input[CONF_BATCH_INFO_FG],
                beer_style=user_input[CONF_BATCH_INFO_BEER_STYLE],
                is_logging_data=user_input[CONF_BATCH_INFO_STARTED],
            )
            await self.coordinator.async_request_refresh()
            return self.async_create_entry(title="Batch Info", data=user_input)
        batch_info = ferminator.batch_info
        is_started = ferminator.is_logging_data

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_BATCH_INFO_BREW_NAME,
                    default=batch_info.brew_name
                    if batch_info is not None
                    else "Unknown",
                ): str,
                vol.Required(
                    CONF_BATCH_INFO_OWNER,
                    default=batch_info.owner if batch_info is not None else "Unknown",
                ): str,
                vol.Required(
                    CONF_BATCH_INFO_EBC,
                    default=batch_info.ebc if batch_info is not None else 0.0,
                ): vol.All(
                    vol.Coerce(int),
                    vol.Range(min=0, max=1000),
                ),
                vol.Required(
                    CONF_BATCH_INFO_IBU,
                    default=batch_info.ibu if batch_info is not None else 0.0,
                ): vol.All(
                    vol.Coerce(int),
                    vol.Range(min=0, max=1000),
                ),
                vol.Required(
                    CONF_BATCH_INFO_VOLUME,
                    default=batch_info.volume if batch_info is not None else 20.0,
                ): vol.All(
                    vol.Coerce(int),
                    vol.Range(min=0, max=100),
                ),
                vol.Required(
                    CONF_BATCH_INFO_OG,
                    default=batch_info.og if batch_info is not None else 1.0,
                ): vol.All(
                    # TODO Make additional validation work
                    # cv.matches_regex(r"^[0|1](\.\d{1,3})?$"),
                    vol.Coerce(float),
                    vol.Range(min=0.98, max=1.999),
                ),
                vol.Required(
                    CONF_BATCH_INFO_FG,
                    default=batch_info.fg if batch_info is not None else 1.0,
                ): vol.All(
                    # TODO Make additional validation work
                    # cv.matches_regex(r"^[0|1](\.\d{1,3})?$"),
                    vol.Coerce(float),
                    vol.Range(min=0, max=1.999),
                ),
                vol.Required(
                    CONF_BATCH_INFO_FERMENTATION_TYPE,
                    default=batch_info.fermentation_type.value
                    if batch_info is not None
                    else FermentationType.TOP.value,
                ): vol.In([ft.value for ft in FermentationType]),
                vol.Required(
                    CONF_BATCH_INFO_BEER_STYLE,
                    default=batch_info.beer_style
                    if batch_info is not None
                    else "Unknown",
                ): str,
                vol.Required(
                    CONF_BATCH_INFO_STARTED,
                    default=is_started,
                ): bool,
            }
        )

        return self.async_show_form(step_id="init", data_schema=schema)
