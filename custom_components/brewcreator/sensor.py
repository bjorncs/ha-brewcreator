"""Sensor entities for Ferminator and hydrometer devices such as Tilt and Graviator."""

from abc import ABC
from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfTemperature, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import FermentationType
from .coordinator import BrewCreatorDataUpdateCoordinator
from .entity import (
    FerminatorEntity,
    HydrometerEntity,
    register_ferminator_entities,
    register_hydrometer_entities,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
):
    """Set up the hydrometer (Tilt, Graviator) and Ferminator sensors."""
    register_ferminator_entities(
        entry,
        async_add_entities,
        lambda coordinator, id: [
            FerminatorLastActivityEntity(coordinator, id),
            FerminatorBrewDateEntity(coordinator, id),
            FerminatorOwnerEntity(coordinator, id),
            FerminatorEbcEntity(coordinator, id),
            FerminatorIbuEntity(coordinator, id),
            FerminatorBatchVolumeEntity(coordinator, id),
            FerminatorFermentationTypeEntity(coordinator, id),
            FerminatorBeerStyleEntity(coordinator, id),
        ],
    )
    register_hydrometer_entities(
        entry,
        async_add_entities,
        lambda coordinator, id: [
            HydrometerTemperatureEntity(coordinator, id),
            HydrometerSpecificGravityEntity(coordinator, id),
            HydrometerLastActivityEntity(coordinator, id),
            HydrometerAbvEntity(coordinator, id),
        ],
    )


class HydrometerSensorEntity(HydrometerEntity, SensorEntity, ABC):
    def __init__(
        self,
        coordinator: BrewCreatorDataUpdateCoordinator,
        id: str,
        name: str,
        unique_id_suffix: str,
    ) -> None:
        super().__init__(coordinator, id, name, unique_id_suffix)


class HydrometerTemperatureEntity(HydrometerSensorEntity):
    def __init__(
        self,
        coordinator: BrewCreatorDataUpdateCoordinator,
        id: str,
    ) -> None:
        super().__init__(coordinator, id, "Temperature", "temp")
        self._attr_device_class = SensorDeviceClass.TEMPERATURE
        self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
        self._attr_suggested_display_precision = 0
        self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> float | None:
        return self._hydrometer().actual_temperature


class HydrometerLastActivityEntity(HydrometerSensorEntity):
    def __init__(
        self,
        coordinator: BrewCreatorDataUpdateCoordinator,
        id: str,
    ) -> None:
        super().__init__(coordinator, id, "Last Activity", "last_activity")
        self._attr_device_class = SensorDeviceClass.TIMESTAMP
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_entity_registry_enabled_default = False
        self._attr_state_class = None

    @property
    def native_value(self) -> datetime | None:
        return self._hydrometer().last_activity_time

    @property
    def available(self) -> bool:
        return self.native_value is not None


class HydrometerSpecificGravityEntity(HydrometerSensorEntity):
    def __init__(
        self,
        coordinator: BrewCreatorDataUpdateCoordinator,
        id: str,
    ) -> None:
        super().__init__(coordinator, id, "Specific Gravity", "sg")
        self._attr_suggested_display_precision = 3
        self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> float | None:
        return self._hydrometer().specific_gravity


class HydrometerAbvEntity(HydrometerSensorEntity):
    def __init__(
        self,
        coordinator: BrewCreatorDataUpdateCoordinator,
        id: str,
    ) -> None:
        super().__init__(coordinator, id, "ABV", "abv")
        self._attr_suggested_display_precision = 1
        self._attr_native_unit_of_measurement = "%"
        self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> float | None:
        return self._hydrometer().abv


class FerminatorSensorEntity(FerminatorEntity, SensorEntity, ABC):
    def __init__(
        self,
        coordinator: BrewCreatorDataUpdateCoordinator,
        id: str,
        name: str,
        unique_id_suffix: str,
    ):
        super().__init__(coordinator, id, name, unique_id_suffix)


class FerminatorLastActivityEntity(FerminatorSensorEntity):
    def __init__(
        self,
        coordinator: BrewCreatorDataUpdateCoordinator,
        id: str,
    ) -> None:
        super().__init__(coordinator, id, "Last Activity", "last_activity")
        self._attr_state_class = None
        self._attr_device_class = SensorDeviceClass.TIMESTAMP
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_entity_registry_enabled_default = False

    @property
    def native_value(self) -> datetime | None:
        return self._ferminator().last_activity_time

    @property
    def available(self) -> bool:
        return self.native_value is not None


class FerminatorBrewDateEntity(FerminatorSensorEntity):
    def __init__(
        self,
        coordinator: BrewCreatorDataUpdateCoordinator,
        id: str,
    ) -> None:
        super().__init__(coordinator, id, "Brew Start Date", "brew_date")
        self._attr_state_class = None
        self._attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def native_value(self) -> datetime | None:
        batch_info = self._ferminator().batch_info
        return batch_info.brew_date if batch_info is not None else None


class FerminatorOwnerEntity(FerminatorSensorEntity):
    def __init__(
        self,
        coordinator: BrewCreatorDataUpdateCoordinator,
        id: str,
    ) -> None:
        super().__init__(coordinator, id, "Owner", "owner")
        self._attr_state_class = None
        self._attr_device_class = None

    @property
    def native_value(self) -> str | None:
        batch_info = self._ferminator().batch_info
        return batch_info.owner if batch_info is not None else None


class FerminatorEbcEntity(FerminatorSensorEntity):
    def __init__(
        self,
        coordinator: BrewCreatorDataUpdateCoordinator,
        id: str,
    ) -> None:
        super().__init__(coordinator, id, "EBC", "ebc")
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_unit_of_measurement = "EBC"

    @property
    def native_value(self) -> float | None:
        batch_info = self._ferminator().batch_info
        return batch_info.ebc if batch_info is not None else None


class FerminatorIbuEntity(FerminatorSensorEntity):
    def __init__(
        self,
        coordinator: BrewCreatorDataUpdateCoordinator,
        id: str,
    ) -> None:
        super().__init__(coordinator, id, "IBU", "ibu")
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_unit_of_measurement = "IBU"

    @property
    def native_value(self) -> float | None:
        batch_info = self._ferminator().batch_info
        return batch_info.ibu if batch_info is not None else None


class FerminatorBatchVolumeEntity(FerminatorSensorEntity):
    def __init__(
        self,
        coordinator: BrewCreatorDataUpdateCoordinator,
        id: str,
    ) -> None:
        super().__init__(coordinator, id, "Batch Volume", "batch_volume")
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_device_class = SensorDeviceClass.VOLUME_STORAGE
        self._attr_native_unit_of_measurement = UnitOfVolume.LITERS

    @property
    def native_value(self) -> float | None:
        batch_info = self._ferminator().batch_info
        return batch_info.volume if batch_info is not None else None


class FerminatorFermentationTypeEntity(FerminatorSensorEntity):
    def __init__(
        self,
        coordinator: BrewCreatorDataUpdateCoordinator,
        id: str,
    ) -> None:
        super().__init__(coordinator, id, "Fermentation Type", "fermentation_type")
        self._attr_state_class = None
        self._attr_device_class = SensorDeviceClass.ENUM
        self._attr_options = [ft.value for ft in FermentationType]

    @property
    def native_value(self) -> str | None:
        batch_info = self._ferminator().batch_info
        return batch_info.fermentation_type.value if batch_info is not None else None


class FerminatorBeerStyleEntity(FerminatorSensorEntity):
    def __init__(
        self,
        coordinator: BrewCreatorDataUpdateCoordinator,
        id: str,
    ) -> None:
        super().__init__(coordinator, id, "Beer Style", "beer_style")
        self._attr_state_class = None
        self._attr_device_class = None

    @property
    def native_value(self) -> str | None:
        batch_info = self._ferminator().batch_info
        return batch_info.beer_style if batch_info is not None else None
