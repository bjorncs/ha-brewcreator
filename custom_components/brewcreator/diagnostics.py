from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from .coordinator import BrewCreatorDataUpdateCoordinator

# User ID, the brewer's name, the webhook (Cloud URL) and serial numbers, matched on any key
TO_REDACT = {
    CONF_USERNAME,
    CONF_PASSWORD,
    "userId",
    "owner",
    "webhook",
    "iotHubBrewEquipmentId",
    "deviceId",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry[BrewCreatorDataUpdateCoordinator]
) -> dict[str, Any]:
    coordinator = entry.runtime_data
    return async_redact_data(
        {
            "coordinator": coordinator.diagnostics(),
            # All equipment as last fetched, every page, as the integration sees it
            "equipments": [e.json for e in coordinator.data.equipment.values()],
        },
        TO_REDACT,
    )
