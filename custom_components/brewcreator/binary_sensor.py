"""Binary sensor entities for Ferminator devices."""

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
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
        lambda coordinator, id: [FerminatorBatchStartedEntity(coordinator, id)],
    )


class FerminatorBatchStartedEntity(FerminatorEntity, BinarySensorEntity):
    """Whether the brew is started, so BrewCreator logs its readings. Changed with the set_batch_info action."""

    def __init__(self, coordinator: BrewCreatorDataUpdateCoordinator, id: str) -> None:
        super().__init__(coordinator, id, "Batch Started", "batch")
        self._attr_device_class = BinarySensorDeviceClass.RUNNING

    @property
    def is_on(self) -> bool | None:
        return self._ferminator().is_logging_data
