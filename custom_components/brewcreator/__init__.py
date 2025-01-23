"""The BrewCreator integration."""

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import BrewCreatorAPI
from .coordinator import BrewCreatorDataUpdateCoordinator
from .token_store import BrewCreatorTokenStore

PLATFORMS: list[Platform] = [
    Platform.CLIMATE,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.TEXT,
]

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry[BrewCreatorDataUpdateCoordinator]
) -> bool:
    """Set up devices from BrewCreator such as Ferminator and Tilt"""
    api = BrewCreatorAPI(
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
        BrewCreatorTokenStore(hass),
        async_get_clientsession(hass),
    )
    coordinator = BrewCreatorDataUpdateCoordinator(hass, api, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: ConfigEntry[BrewCreatorDataUpdateCoordinator]
) -> bool:
    """Unload a config entry."""
    coordinator = entry.runtime_data
    await coordinator.close()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
