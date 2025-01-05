"""Sensor entities for Tilt devices."""

from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import Ferminator, Tilt
from .const import DOMAIN
from .coordinator import BrewCreatorDataUpdateCoordinator
from .device import ferminator_device_info, tilt_device_info


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[BrewCreatorDataUpdateCoordinator],
    async_add_entities: AddEntitiesCallback,
):
    """Set up the Tilt Hydrometer and Ferminator sensors."""
    coordinator: BrewCreatorDataUpdateCoordinator = entry.runtime_data
    for equipment in coordinator.data.values():
        if isinstance(equipment, Tilt):
            async_add_entities(
                [
                    TiltSensorEntity(
                        coordinator,
                        equipment.id,
                        SensorDeviceClass.TEMPERATURE,
                    ),
                    TiltSensorEntity(
                        coordinator,
                        equipment.id,
                        None,
                    ),
                    TiltSensorEntity(
                        coordinator,
                        equipment.id,
                        SensorDeviceClass.TIMESTAMP,
                    ),
                ],
                True,
            )
        elif isinstance(equipment, Ferminator):
            async_add_entities(
                [FerminatorSensorEntity(coordinator, equipment.id)], True
            )


class TiltSensorEntity(CoordinatorEntity, SensorEntity):
    def __init__(
        self,
        coordinator: BrewCreatorDataUpdateCoordinator,
        id: str,
        device_class: SensorDeviceClass | None,
    ):
        super().__init__(coordinator)
        self._brewcreator_id = id
        self._attr_device_info = tilt_device_info(self.__tilt())
        self._attr_device_class = device_class
        self._attr_state_class = SensorStateClass.MEASUREMENT
        if device_class == SensorDeviceClass.TEMPERATURE:
            self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
            self._attr_name = "Temperature"
            self._attr_suggested_display_precision = 0
            unique_id_suffix = "temp"
        elif device_class == SensorDeviceClass.TIMESTAMP:
            self._attr_name = "Last Activity"
            unique_id_suffix = "last_activity"
        else:
            self._attr_name = "Specific Gravity"
            self._attr_suggested_display_precision = 3
            unique_id_suffix = "sg"
        self._attr_has_entity_name = True
        self._attr_unique_id = f"{DOMAIN}_{id}_{unique_id_suffix}"

    @property
    def native_value(self) -> float | datetime | None:
        if self.device_class == SensorDeviceClass.TEMPERATURE:
            return self.__tilt().actual_temperature
        if self.device_class == SensorDeviceClass.TIMESTAMP:
            return self.__tilt().last_activity_time
        return self.__tilt().specific_gravity

    @property
    def available(self) -> bool:
        return self.__tilt().is_active

    def __tilt(self) -> Tilt:
        return self.coordinator.data[self._brewcreator_id]


class FerminatorSensorEntity(CoordinatorEntity, SensorEntity):
    def __init__(
        self,
        coordinator: BrewCreatorDataUpdateCoordinator,
        id: str,
    ):
        super().__init__(coordinator)
        self._brewcreator_id = id
        self._attr_device_info = ferminator_device_info(self.__ferminator())
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_device_class = SensorDeviceClass.TIMESTAMP
        self._attr_name = "Last Activity"
        self._attr_unique_id = f"{DOMAIN}_{id}_last_activity"

    @property
    def native_value(self) -> datetime | None:
        return self.__ferminator().last_activity_time

    @property
    def available(self) -> bool:
        return self.__ferminator().is_active

    def __ferminator(self) -> Ferminator:
        return self.coordinator.data[self._brewcreator_id]
