from abc import ABC
from collections.abc import Callable
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import (
    BrewCreatorEquipment,
    Ferminator,
    Graviator,
    Hydrometer,
    linked_ferminator,
)
from .const import DOMAIN
from .coordinator import BrewCreatorDataUpdateCoordinator
from .device import device_info

_LOGGER = logging.getLogger(__name__)

_UNIQUE_ID_PREFIX = f"{DOMAIN}_"


def entity_unique_id(equipment_id: str, suffix: str) -> str:
    """Return an entity's unique ID, brewcreator_<equipment id>_<suffix>."""
    return f"{_UNIQUE_ID_PREFIX}{equipment_id}_{suffix}"


def equipment_id_from_unique_id(unique_id: str) -> str | None:
    """Return the equipment ID of an entity unique ID made by entity_unique_id().

    Equipment IDs are UUIDs, which contain no underscores. The suffix may contain some, or be empty.
    """
    if not unique_id.startswith(_UNIQUE_ID_PREFIX):
        return None
    equipment_id, separator, _ = unique_id[len(_UNIQUE_ID_PREFIX) :].partition("_")
    if not separator or not equipment_id:
        return None
    return equipment_id


class BrewCreatorEntity(CoordinatorEntity[BrewCreatorDataUpdateCoordinator], ABC):
    def __init__(
        self,
        coordinator: BrewCreatorDataUpdateCoordinator,
        id: str,
        name: str,
        unique_id_suffix: str,
    ):
        super().__init__(coordinator)
        self._brewcreator_id = id
        self._attr_has_entity_name = True
        self._attr_name = name
        self._attr_unique_id = entity_unique_id(id, unique_id_suffix)
        _LOGGER.debug(
            "Creating entity (id=%s, class=%s, name=%s)",
            self._attr_unique_id,
            self.__class__.__name__,
            name,
        )

    @property
    def available(self) -> bool:
        return self._equipment_available

    @property
    def _equipment_available(self) -> bool:
        """Return whether the last refresh succeeded and the equipment is still in BrewCreator."""
        return (
            self.coordinator.last_update_success
            and self._brewcreator_id in self.coordinator.data.equipment
        )

    @property
    def _brewcreator_device(self) -> BrewCreatorEquipment:
        return self.coordinator.data.equipment[self._brewcreator_id]


class HydrometerEntity(BrewCreatorEntity, ABC):
    def __init__(
        self,
        coordinator: BrewCreatorDataUpdateCoordinator,
        id: str,
        name: str,
        unique_id_suffix: str,
    ):
        super().__init__(coordinator, id, name, unique_id_suffix)
        self._attr_device_info = device_info(self._hydrometer())

    @property
    def available(self) -> bool:
        """Return whether the hydrometer is available, based on its link to a Ferminator.

        A Tilt can only report through a Ferminator, so it needs to be linked to one that is connected.
        A Graviator linked to a Ferminator follows the same rule, while an unlinked one reports over Wi-Fi
        and is available while it has a valid reading.
        """
        if not self._equipment_available:
            return False
        hydrometer = self._hydrometer()
        ferminator = linked_ferminator(hydrometer, self.coordinator.data.equipment)
        if ferminator is not None:
            has_data = (
                hydrometer.specific_gravity is not None
                or hydrometer.actual_temperature is not None
            )
            return has_data and ferminator.is_connected
        if isinstance(hydrometer, Graviator):
            return hydrometer.specific_gravity is not None
        return False

    def _hydrometer(self) -> Hydrometer:
        return self._brewcreator_device


class FerminatorEntity(BrewCreatorEntity, ABC):
    def __init__(
        self,
        coordinator: BrewCreatorDataUpdateCoordinator,
        id: str,
        name: str,
        unique_id_suffix: str,
    ):
        super().__init__(coordinator, id, name, unique_id_suffix)
        self._attr_device_info = device_info(self._ferminator())

    @property
    def available(self) -> bool:
        return self._equipment_available and self._ferminator().is_connected

    def _ferminator(self) -> Ferminator:
        return self._brewcreator_device


CreateEntities = Callable[[BrewCreatorDataUpdateCoordinator, str], list[BrewCreatorEntity]]
"""Creates the entities of one piece of equipment, given its equipment ID."""


def register_ferminator_entities(
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
    create_ferminator_entities: CreateEntities,
):
    """Add entities for every Ferminator, now and when one appears later."""
    _register_entities(entry, async_add_entities, Ferminator, create_ferminator_entities)


def register_hydrometer_entities(
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
    create_hydrometer_entities: CreateEntities,
):
    """Add entities for every hydrometer, now and when one appears later."""
    _register_entities(entry, async_add_entities, Hydrometer, create_hydrometer_entities)


def _register_entities(
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
    equipment_type: type[BrewCreatorEquipment],
    create_entities: CreateEntities,
):
    coordinator: BrewCreatorDataUpdateCoordinator = entry.runtime_data

    @callback
    def add_entities(equipment: list[BrewCreatorEquipment]) -> set[str]:
        """Add entities for the equipment, returning the IDs whose entities could not be created."""
        entities = []
        failed = set()
        for e in equipment:
            if not isinstance(e, equipment_type):
                continue
            try:
                entities.extend(create_entities(coordinator, e.id))
            except Exception:
                # Don't let one piece of equipment with unexpected data keep the others from being added
                _LOGGER.exception("Failed to create entities for %s", e.name)
                failed.add(e.id)
        if entities:
            async_add_entities(entities, update_before_add=False)
        return failed

    entry.async_on_unload(coordinator.async_add_equipment_listener(add_entities))

