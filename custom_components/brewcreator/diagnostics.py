from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .coordinator import BrewCreatorDataUpdateCoordinator


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry[BrewCreatorDataUpdateCoordinator]) -> dict[str, Any]:
    return {
        "equipments": await entry.runtime_data.api.equipment_json()
    }
