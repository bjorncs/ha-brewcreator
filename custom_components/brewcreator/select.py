"""Select entity for choosing the hydrometer linked to a Ferminator."""

from collections import Counter
from collections.abc import Mapping

import aiohttp

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import (
    BrewCreatorEquipment,
    BrewCreatorError,
    Ferminator,
    Hydrometer,
    linkable_hydrometers,
)
from .const import DOMAIN
from .coordinator import BrewCreatorDataUpdateCoordinator
from .entity import FerminatorEntity, register_ferminator_entities

NO_HYDROMETER = "none"


def hydrometers_by_option(
    ferminator: Ferminator, equipment: Mapping[str, BrewCreatorEquipment]
) -> dict[str, Hydrometer]:
    """Return the hydrometers that can be linked to the Ferminator, by their option in the Hydrometer select.

    An option is the hydrometer's name, with the start of its equipment ID when two share a name.
    """
    hydrometers = linkable_hydrometers(ferminator, equipment)
    name_counts = Counter(h.name for h in hydrometers)
    return {
        h.name if name_counts[h.name] == 1 else f"{h.name} ({h.id[:8]})": h
        for h in hydrometers
    }


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
):
    register_ferminator_entities(
        entry,
        async_add_entities,
        lambda coordinator, id: [FerminatorHydrometerEntity(coordinator, id)],
    )


class FerminatorHydrometerEntity(FerminatorEntity, SelectEntity):
    """The Tilt or Graviator linked to the Ferminator, like "Connect Device" in the BrewCreator web app.

    Offers the hydrometers not linked to another Ferminator. Hydrometers are shown by name, with the start of their
    equipment ID when two share a name.
    """

    def __init__(self, coordinator: BrewCreatorDataUpdateCoordinator, id: str) -> None:
        super().__init__(coordinator, id, "Hydrometer", "hydrometer")
        self._attr_entity_category = EntityCategory.CONFIG
        # Translates the "none" option
        self._attr_translation_key = "hydrometer"

    def _hydrometers_by_option(self) -> dict[str, Hydrometer]:
        # capability_attributes reads the options even while the entity is unavailable, e.g. once the Ferminator is
        # gone from BrewCreator
        if self._brewcreator_id not in self.coordinator.data.equipment:
            return {}
        return hydrometers_by_option(self._ferminator(), self.coordinator.data.equipment)

    @property
    def options(self) -> list[str]:
        return [NO_HYDROMETER, *self._hydrometers_by_option()]

    @property
    def current_option(self) -> str | None:
        linked = self._ferminator().linked_hydrometers
        if not linked:
            return NO_HYDROMETER
        return next(
            (option for option, h in self._hydrometers_by_option().items() if h.id == linked[0].id),
            None,
        )

    async def async_select_option(self, option: str) -> None:
        hydrometer = None if option == NO_HYDROMETER else self._hydrometers_by_option()[option]
        ferminator = self._ferminator()
        try:
            await ferminator.link_hydrometer(hydrometer)
        except (BrewCreatorError, aiohttp.ClientError, TimeoutError) as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="link_failed",
                translation_placeholders={"name": ferminator.name, "error": str(err)},
            ) from err
        # Refresh at once, so the new link shows when the action returns
        await self.coordinator.async_refresh()
