"""Sensor entities for Ferminator and hydrometer devices such as Tilt and Graviator."""

from abc import ABC, abstractmethod
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

from .api import BatchInfo, FermentationType
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
            FerminatorProbeTemperatureEntity(coordinator, id),
            FerminatorLastActivityEntity(coordinator, id),
            FerminatorBrewDateEntity(coordinator, id),
            FerminatorBatchNameEntity(coordinator, id),
            FerminatorOriginalGravityEntity(coordinator, id),
            FerminatorFinalGravityEntity(coordinator, id),
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
            HydrometerLastBrewLogEntryEntity(coordinator, id),
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
        self._attr_suggested_display_precision = 1
        self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> float | None:
        return self._hydrometer().actual_temperature


class HydrometerLastBrewLogEntryEntity(HydrometerSensorEntity):
    """Time of the newest brew log point, which BrewCreator reports as the hydrometer's last activity."""

    def __init__(
        self,
        coordinator: BrewCreatorDataUpdateCoordinator,
        id: str,
    ) -> None:
        # The unique ID suffix is kept from when this was named "Last Activity", so the entity and its history stay
        super().__init__(coordinator, id, "Last Brew Log Entry", "last_activity")
        self._attr_device_class = SensorDeviceClass.TIMESTAMP
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_entity_registry_enabled_default = False
        self._attr_state_class = None

    @property
    def native_value(self) -> datetime | None:
        return self._hydrometer().last_activity_time

    @property
    def available(self) -> bool:
        # Meaningful whether or not the hydrometer is linked
        return self._equipment_available and self.native_value is not None


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


class FerminatorProbeTemperatureEntity(FerminatorSensorEntity):
    """Temperature from the Ferminator's built-in probe.

    The climate entity reports the linked hydrometer's temperature instead when it has a reading.
    """

    def __init__(
        self,
        coordinator: BrewCreatorDataUpdateCoordinator,
        id: str,
    ) -> None:
        super().__init__(coordinator, id, "Built-in Probe Temperature", "probe_temp")
        self._attr_device_class = SensorDeviceClass.TEMPERATURE
        self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
        self._attr_suggested_display_precision = 1
        self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> float | None:
        return self._ferminator().actual_temperature_builtin_probe


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
        return self._equipment_available and self.native_value is not None


class FerminatorBatchSensorEntity(FerminatorSensorEntity, ABC):
    """A field of the Ferminator's batch info, unknown while it has no batch."""

    @property
    def native_value(self):
        batch_info = self._ferminator().batch_info
        return self._batch_value(batch_info) if batch_info is not None else None

    @abstractmethod
    def _batch_value(self, batch_info: BatchInfo): ...


class FerminatorBrewDateEntity(FerminatorBatchSensorEntity):
    def __init__(self, coordinator: BrewCreatorDataUpdateCoordinator, id: str) -> None:
        super().__init__(coordinator, id, "Brew Start Date", "brew_date")
        self._attr_device_class = SensorDeviceClass.TIMESTAMP

    def _batch_value(self, batch_info: BatchInfo) -> datetime | None:
        return batch_info.brew_date


class FerminatorOwnerEntity(FerminatorBatchSensorEntity):
    def __init__(self, coordinator: BrewCreatorDataUpdateCoordinator, id: str) -> None:
        super().__init__(coordinator, id, "Owner", "owner")

    def _batch_value(self, batch_info: BatchInfo) -> str | None:
        return batch_info.owner


class FerminatorEbcEntity(FerminatorBatchSensorEntity):
    def __init__(self, coordinator: BrewCreatorDataUpdateCoordinator, id: str) -> None:
        super().__init__(coordinator, id, "EBC", "ebc")
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_unit_of_measurement = "EBC"

    def _batch_value(self, batch_info: BatchInfo) -> float | None:
        return batch_info.ebc


class FerminatorIbuEntity(FerminatorBatchSensorEntity):
    def __init__(self, coordinator: BrewCreatorDataUpdateCoordinator, id: str) -> None:
        super().__init__(coordinator, id, "IBU", "ibu")
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_unit_of_measurement = "IBU"

    def _batch_value(self, batch_info: BatchInfo) -> float | None:
        return batch_info.ibu


class FerminatorBatchVolumeEntity(FerminatorBatchSensorEntity):
    def __init__(self, coordinator: BrewCreatorDataUpdateCoordinator, id: str) -> None:
        super().__init__(coordinator, id, "Batch Volume", "batch_volume")
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_device_class = SensorDeviceClass.VOLUME_STORAGE
        self._attr_native_unit_of_measurement = UnitOfVolume.LITERS

    def _batch_value(self, batch_info: BatchInfo) -> float | None:
        return batch_info.volume


class FerminatorFermentationTypeEntity(FerminatorBatchSensorEntity):
    def __init__(self, coordinator: BrewCreatorDataUpdateCoordinator, id: str) -> None:
        super().__init__(coordinator, id, "Fermentation Type", "fermentation_type")
        self._attr_device_class = SensorDeviceClass.ENUM
        self._attr_options = [ft.value for ft in FermentationType]

    def _batch_value(self, batch_info: BatchInfo) -> str | None:
        return batch_info.fermentation_type.value


class FerminatorBeerStyleEntity(FerminatorBatchSensorEntity):
    def __init__(self, coordinator: BrewCreatorDataUpdateCoordinator, id: str) -> None:
        super().__init__(coordinator, id, "Beer Style", "beer_style")

    def _batch_value(self, batch_info: BatchInfo) -> str | None:
        return batch_info.beer_style


class FerminatorBatchNameEntity(FerminatorBatchSensorEntity):
    def __init__(self, coordinator: BrewCreatorDataUpdateCoordinator, id: str) -> None:
        super().__init__(coordinator, id, "Batch Name", "batch_name")

    def _batch_value(self, batch_info: BatchInfo) -> str | None:
        return batch_info.brew_name


class FerminatorOriginalGravityEntity(FerminatorBatchSensorEntity):
    def __init__(self, coordinator: BrewCreatorDataUpdateCoordinator, id: str) -> None:
        # Batch info rather than a measurement, so it has no state class
        super().__init__(coordinator, id, "Original Gravity", "original_gravity")
        self._attr_suggested_display_precision = 3

    def _batch_value(self, batch_info: BatchInfo) -> float | None:
        return batch_info.og


class FerminatorFinalGravityEntity(FerminatorBatchSensorEntity):
    def __init__(self, coordinator: BrewCreatorDataUpdateCoordinator, id: str) -> None:
        # Batch info rather than a measurement, so it has no state class
        super().__init__(coordinator, id, "Final Gravity", "final_gravity")
        self._attr_suggested_display_precision = 3

    def _batch_value(self, batch_info: BatchInfo) -> float | None:
        return batch_info.fg
