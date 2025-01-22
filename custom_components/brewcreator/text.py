from homeassistant.components.text import TextEntity
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
        lambda coordinator, id: [FerminatorBatchNameEntity(coordinator, id)],
    )


class FerminatorBatchNameEntity(FerminatorEntity, TextEntity):
    def __init__(self, coordinator: BrewCreatorDataUpdateCoordinator, id: str) -> None:
        super().__init__(coordinator, id, "Batch Name", "batch_name")

    @property
    def native_value(self) -> str | None:
        batch_info = self._ferminator().batch_info
        return batch_info.brew_name if batch_info is not None else None

    async def async_set_value(self, value: str) -> None:
        await self._ferminator().set_batch_info(brew_name=value)
        await self.coordinator.async_request_refresh()
