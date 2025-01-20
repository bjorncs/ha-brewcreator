from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import BrewCreatorDataUpdateCoordinator
from .entity import FerminatorEntity, register_ferminator_entities


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
):
    register_ferminator_entities(
        entry,
        async_add_entities,
        lambda coordinator, id: [FerminatorBatchSwitchEntity(coordinator, id)],
    )


class FerminatorBatchSwitchEntity(FerminatorEntity, SwitchEntity):
    def __init__(self, coordinator: BrewCreatorDataUpdateCoordinator, id: str):
        super().__init__(coordinator, id, "Batch Started", "batch")
        self._attr_device_class = SwitchDeviceClass.SWITCH

    @property
    def is_on(self) -> bool:
        return self._ferminator().is_logging_data

    async def async_turn_on(self, **kwargs) -> None:
        await self._ferminator().set_logging_data(True)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        await self._ferminator().set_logging_data(False)
        await self.coordinator.async_request_refresh()
