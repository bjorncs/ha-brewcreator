import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from custom_components.brewcreator import BrewCreatorEquipment, BrewCreatorAPI, DOMAIN

_LOGGER = logging.getLogger(__name__)


class BrewCreatorDataUpdateCoordinator(DataUpdateCoordinator[dict[str, BrewCreatorEquipment]]):
    """Coordinator for updating data from BrewCreator API."""

    def __init__(
        self,
        hass: HomeAssistant,
        api: BrewCreatorAPI,
        entry: ConfigEntry['BrewCreatorDataUpdateCoordinator'],
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            always_update=True,
            update_interval=None,
            update_method=None,
            config_entry=entry,
        )
        self._api: BrewCreatorAPI = api

    async def _async_setup(self):
        await self._api.start_websocket(self._on_equipment_update)

    async def _async_update_data(self) -> dict[str, BrewCreatorEquipment]:
        return await self._api.list_equipment()

    async def _on_equipment_update(
        self, equipment_list: dict[str, BrewCreatorEquipment]
    ) -> None:
        _LOGGER.debug("Received equipment update: %s", equipment_list)
        self.async_set_updated_data(equipment_list)

    @property
    def api(self) -> BrewCreatorAPI:
        return self._api

    async def close(self) -> None:
        await self._api.close()
