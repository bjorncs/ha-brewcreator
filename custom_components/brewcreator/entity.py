import logging
from abc import ABC
from collections.abc import Callable
from typing import cast

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import BrewCreatorEquipment, EquipmentTypes, Ferminator
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
    _requires_connection = True
    """Whether the entity is only available while its equipment is connected, see is_connected of the equipment."""

    def __init__(
        self,
        coordinator: BrewCreatorDataUpdateCoordinator,
        id: str,
        translation_key: str | None,
        unique_id_suffix: str,
    ):
        super().__init__(coordinator)
        self._brewcreator_id = id
        self._attr_has_entity_name = True
        # The name comes from the translation key, or from the device class when it has none
        self._attr_translation_key = translation_key
        self._attr_unique_id = entity_unique_id(id, unique_id_suffix)
        self._attr_device_info = device_info(self._brewcreator_device)
        _LOGGER.debug(
            "Creating entity (id=%s, class=%s, translation_key=%s)",
            self._attr_unique_id,
            self.__class__.__name__,
            translation_key,
        )

    @property
    def available(self) -> bool:
        return self._equipment_available and (not self._requires_connection or self._brewcreator_device.is_connected)

    @property
    def _equipment_available(self) -> bool:
        """Return whether the last refresh succeeded and the equipment is still in BrewCreator."""
        return self.coordinator.last_update_success and self._brewcreator_id in self.coordinator.data.equipment

    @property
    def _brewcreator_device(self) -> BrewCreatorEquipment:
        return self.coordinator.data.equipment[self._brewcreator_id]


class FerminatorEntity(BrewCreatorEntity, ABC):
    def _ferminator(self) -> Ferminator:
        # Ferminator entities are only created for Ferminators
        return cast(Ferminator, self._brewcreator_device)


class BatchInfoEntity(BrewCreatorEntity, ABC):
    """An entity showing the batch info of a Ferminator or an unlinked Graviator.

    Batch info is metadata kept in BrewCreator and set through its API (set_batch_info), not something the equipment
    reports, so it stays available while the equipment is offline. A Graviator linked to a Ferminator has a copy of the
    Ferminator's batch info, which the Ferminator's entities show, so its own are unavailable while it is linked.
    """

    _requires_connection = False

    @property
    def available(self) -> bool:
        return super().available and self._brewcreator_device.batch_owner is self._brewcreator_device


CreateEntities = Callable[[BrewCreatorDataUpdateCoordinator, str], list[BrewCreatorEntity]]
"""Creates the entities of one piece of equipment, given its equipment ID."""


def register_entities(
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
    equipment_type: EquipmentTypes,
    create_entities: CreateEntities,
):
    """Add entities for all equipment of the type, now and when some appears later.

    Batch info entities are added for BATCH_INFO_EQUIPMENT: Tilts get none, as they only report through a Ferminator,
    whose entities show the batch.
    """
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
