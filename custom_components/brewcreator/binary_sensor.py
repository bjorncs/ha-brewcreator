"""Binary sensor entities for the batch info of Ferminators and Graviators."""

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import BrewCreatorDataUpdateCoordinator
from .entity import BatchInfoEntity, register_batch_info_entities


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
):
    register_batch_info_entities(
        entry,
        async_add_entities,
        lambda coordinator, id: [BatchStartedEntity(coordinator, id)],
    )


class BatchStartedEntity(BatchInfoEntity, BinarySensorEntity):
    """Whether the brew is started, so BrewCreator logs its readings. Changed with the set_batch_info action."""

    def __init__(self, coordinator: BrewCreatorDataUpdateCoordinator, id: str) -> None:
        super().__init__(coordinator, id, "batch_started", "batch")
        self._attr_device_class = BinarySensorDeviceClass.RUNNING

    @property
    def is_on(self) -> bool | None:
        return self._brewcreator_device.is_logging_data
