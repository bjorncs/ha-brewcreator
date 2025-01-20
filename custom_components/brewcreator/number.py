from homeassistant.components.number import NumberEntity, NumberMode
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
        lambda coordinator, id: [
            FerminatorOriginalGravityEntity(coordinator, id),
            FerminatorFinalGravityEntity(coordinator, id),
        ],
    )


class FerminatorNumberEntity(FerminatorEntity, NumberEntity):
    def __init__(
        self,
        coordinator: BrewCreatorDataUpdateCoordinator,
        id: str,
        name: str,
        unique_id_suffix: str,
    ) -> None:
        super().__init__(coordinator, id, name, unique_id_suffix)
        self._attr_mode = NumberMode.BOX
        self._attr_native_max_value = 1.999
        self._attr_native_step = 0.001


class FerminatorOriginalGravityEntity(FerminatorNumberEntity):
    def __init__(self, coordinator: BrewCreatorDataUpdateCoordinator, id: str) -> None:
        super().__init__(coordinator, id, "Original Gravity", "original_gravity")
        self._attr_native_min_value = 0.98

    @property
    def native_value(self) -> float | None:
        batch_info = self._ferminator().batch_info
        return batch_info.og if batch_info is not None else None

    async def async_set_value(self, value: float) -> None:
        await self._ferminator().set_batch_info(og=value)
        await self.coordinator.async_request_refresh()


class FerminatorFinalGravityEntity(FerminatorNumberEntity):
    def __init__(self, coordinator: BrewCreatorDataUpdateCoordinator, id: str) -> None:
        super().__init__(coordinator, id, "Final Gravity", "final_gravity")
        self._attr_native_min_value = 0.0

    @property
    def native_value(self) -> float | None:
        batch_info = self._ferminator().batch_info
        return batch_info.fg if batch_info is not None else None

    async def async_set_native_value(self, value: float) -> None:
        await self._ferminator().set_batch_info(fg=value)
        await self.coordinator.async_request_refresh()
