from abc import ABC
import logging
from typing import Protocol

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import BrewCreatorEquipment, Ferminator, Tilt
from .const import DOMAIN
from .coordinator import BrewCreatorDataUpdateCoordinator
from datetime import datetime, timedelta, timezone

_LOGGER = logging.getLogger(__name__)


def ferminator_device_info(ferminator: Ferminator) -> DeviceInfo:
    """Return device info for Ferminator."""
    return DeviceInfo(
        identifiers={(DOMAIN, ferminator.serial_number)},
        manufacturer="Brewolution",
        serial_number=ferminator.serial_number,
        model=ferminator.equipment_type.value,
        name=ferminator.name,
        sw_version=ferminator.sw_version,
        hw_version=ferminator.hw_version,
    )


def tilt_device_info(tilt: Tilt) -> DeviceInfo:
    """Return device info for Tilt."""
    return DeviceInfo(
        identifiers={(DOMAIN, tilt.serial_number)},
        manufacturer="Tilt",
        serial_number=tilt.serial_number,
        model=tilt.color.name,
        name=tilt.name,
    )


class BrewCreatorEntity(CoordinatorEntity, ABC):
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
        self._attr_unique_id = f"{DOMAIN}_{id}_{unique_id_suffix}"
        _LOGGER.debug(
            "Creating entity (id=%s, class=%s, name=%s)",
            self._attr_unique_id,
            self.__class__.__name__,
            name,
        )

    @property
    def _brewcreator_device(self) -> BrewCreatorEquipment:
        return self.coordinator.data[self._brewcreator_id]


class TiltEntity(BrewCreatorEntity, ABC):
    def __init__(
        self,
        coordinator: BrewCreatorDataUpdateCoordinator,
        id: str,
        name: str,
        unique_id_suffix: str,
    ):
        super().__init__(coordinator, id, name, unique_id_suffix)
        self._attr_device_info = tilt_device_info(self._tilt())

    @property
    def available(self) -> bool:
        """Return whether the tilt is available based on data and activity time."""

        tilt = self._tilt()
        has_data = (
            tilt.specific_gravity is not None or tilt.actual_temperature is not None
        )
        recent_activity = datetime.now(timezone.utc) - tilt.last_activity_time < timedelta(hours=12)

        return has_data and recent_activity

    def _tilt(self) -> Tilt:
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
        self._attr_device_info = ferminator_device_info(self._ferminator())

    @property
    def available(self) -> bool:
        return self._ferminator().is_connected

    def _ferminator(self) -> Ferminator:
        return self._brewcreator_device


class CreateFerminatorEntitiesCallback(Protocol):
    def __call__(
        self, coordinator: BrewCreatorDataUpdateCoordinator, equipment_id: str
    ) -> list[FerminatorEntity]:
        """Create Ferminator entities"""


class CreateTiltEntitiesCallback(Protocol):
    def __call__(
        self, coordinator: BrewCreatorDataUpdateCoordinator, equipment_id: str
    ) -> list[TiltEntity]:
        """Create Tilt entities"""


def register_ferminator_entities(
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
    create_ferminator_entities: CreateFerminatorEntitiesCallback,
):
    coordinator: BrewCreatorDataUpdateCoordinator = entry.runtime_data
    for equipment in coordinator.data.values():
        if isinstance(equipment, Ferminator):
            async_add_entities(
                create_ferminator_entities(coordinator, equipment.id), True
            )


def register_tilt_entities(
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
    create_tilt_entities: CreateTiltEntitiesCallback,
):
    coordinator: BrewCreatorDataUpdateCoordinator = entry.runtime_data
    for equipment in coordinator.data.values():
        if isinstance(equipment, Tilt):
            async_add_entities(create_tilt_entities(coordinator, equipment.id), True)
