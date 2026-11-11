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
    BatchInfoEntity,
    FerminatorEntity,
    HydrometerEntity,
    register_batch_info_entities,
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
        ],
    )
    register_batch_info_entities(
        entry,
        async_add_entities,
        lambda coordinator, id: [
            BrewDateEntity(coordinator, id),
            BatchNameEntity(coordinator, id),
            OriginalGravityEntity(coordinator, id),
            FinalGravityEntity(coordinator, id),
            OwnerEntity(coordinator, id),
            EbcEntity(coordinator, id),
            IbuEntity(coordinator, id),
            BatchVolumeEntity(coordinator, id),
            FermentationTypeEntity(coordinator, id),
            BeerStyleEntity(coordinator, id),
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
        translation_key: str | None,
        unique_id_suffix: str,
    ) -> None:
        super().__init__(coordinator, id, translation_key, unique_id_suffix)


class HydrometerTemperatureEntity(HydrometerSensorEntity):
    def __init__(
        self,
        coordinator: BrewCreatorDataUpdateCoordinator,
        id: str,
    ) -> None:
        super().__init__(coordinator, id, None, "temp")
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
        super().__init__(coordinator, id, "last_brew_log_entry", "last_activity")
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
        super().__init__(coordinator, id, "specific_gravity", "sg")
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
        super().__init__(coordinator, id, "abv", "abv")
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
        translation_key: str | None,
        unique_id_suffix: str,
    ):
        super().__init__(coordinator, id, translation_key, unique_id_suffix)


class FerminatorProbeTemperatureEntity(FerminatorSensorEntity):
    """Temperature from the Ferminator's built-in probe.

    The climate entity reports the linked hydrometer's temperature instead when it has a reading.
    """

    def __init__(
        self,
        coordinator: BrewCreatorDataUpdateCoordinator,
        id: str,
    ) -> None:
        super().__init__(coordinator, id, "probe_temperature", "probe_temp")
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
        super().__init__(coordinator, id, "last_activity", "last_activity")
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


class BatchInfoSensorEntity(BatchInfoEntity, SensorEntity, ABC):
    """A field of the batch info of a Ferminator or an unlinked Graviator, unknown while it has no batch."""

    @property
    def native_value(self):
        batch_info = self._brewcreator_device.batch_info
        return self._batch_value(batch_info) if batch_info is not None else None

    @abstractmethod
    def _batch_value(self, batch_info: BatchInfo): ...


class BrewDateEntity(BatchInfoSensorEntity):
    def __init__(self, coordinator: BrewCreatorDataUpdateCoordinator, id: str) -> None:
        super().__init__(coordinator, id, "brew_date", "brew_date")
        self._attr_device_class = SensorDeviceClass.TIMESTAMP

    def _batch_value(self, batch_info: BatchInfo) -> datetime | None:
        return batch_info.brew_date


class OwnerEntity(BatchInfoSensorEntity):
    def __init__(self, coordinator: BrewCreatorDataUpdateCoordinator, id: str) -> None:
        super().__init__(coordinator, id, "owner", "owner")

    def _batch_value(self, batch_info: BatchInfo) -> str | None:
        return batch_info.owner


class EbcEntity(BatchInfoSensorEntity):
    def __init__(self, coordinator: BrewCreatorDataUpdateCoordinator, id: str) -> None:
        # Batch info rather than a measurement, so it has no state class
        super().__init__(coordinator, id, "ebc", "ebc")
        self._attr_native_unit_of_measurement = "EBC"

    def _batch_value(self, batch_info: BatchInfo) -> float | None:
        return batch_info.ebc


class IbuEntity(BatchInfoSensorEntity):
    def __init__(self, coordinator: BrewCreatorDataUpdateCoordinator, id: str) -> None:
        # Batch info rather than a measurement, so it has no state class
        super().__init__(coordinator, id, "ibu", "ibu")
        self._attr_native_unit_of_measurement = "IBU"

    def _batch_value(self, batch_info: BatchInfo) -> float | None:
        return batch_info.ibu


class BatchVolumeEntity(BatchInfoSensorEntity):
    def __init__(self, coordinator: BrewCreatorDataUpdateCoordinator, id: str) -> None:
        # Batch info rather than a measurement, so it has no state class
        super().__init__(coordinator, id, "batch_volume", "batch_volume")
        self._attr_device_class = SensorDeviceClass.VOLUME_STORAGE
        self._attr_native_unit_of_measurement = UnitOfVolume.LITERS

    def _batch_value(self, batch_info: BatchInfo) -> float | None:
        return batch_info.volume


class FermentationTypeEntity(BatchInfoSensorEntity):
    def __init__(self, coordinator: BrewCreatorDataUpdateCoordinator, id: str) -> None:
        super().__init__(coordinator, id, "fermentation_type", "fermentation_type")
        self._attr_device_class = SensorDeviceClass.ENUM
        self._attr_options = [ft.name.lower() for ft in FermentationType]

    def _batch_value(self, batch_info: BatchInfo) -> str | None:
        fermentation_type = batch_info.fermentation_type
        return fermentation_type.name.lower() if fermentation_type is not None else None


class BeerStyleEntity(BatchInfoSensorEntity):
    def __init__(self, coordinator: BrewCreatorDataUpdateCoordinator, id: str) -> None:
        super().__init__(coordinator, id, "beer_style", "beer_style")

    def _batch_value(self, batch_info: BatchInfo) -> str | None:
        return batch_info.beer_style


class BatchNameEntity(BatchInfoSensorEntity):
    def __init__(self, coordinator: BrewCreatorDataUpdateCoordinator, id: str) -> None:
        super().__init__(coordinator, id, "batch_name", "batch_name")

    def _batch_value(self, batch_info: BatchInfo) -> str | None:
        return batch_info.brew_name


class OriginalGravityEntity(BatchInfoSensorEntity):
    def __init__(self, coordinator: BrewCreatorDataUpdateCoordinator, id: str) -> None:
        # Batch info rather than a measurement, so it has no state class
        super().__init__(coordinator, id, "original_gravity", "original_gravity")
        self._attr_suggested_display_precision = 3

    def _batch_value(self, batch_info: BatchInfo) -> float | None:
        return batch_info.og


class FinalGravityEntity(BatchInfoSensorEntity):
    def __init__(self, coordinator: BrewCreatorDataUpdateCoordinator, id: str) -> None:
        # Batch info rather than a measurement, so it has no state class
        super().__init__(coordinator, id, "final_gravity", "final_gravity")
        self._attr_suggested_display_precision = 3

    def _batch_value(self, batch_info: BatchInfo) -> float | None:
        return batch_info.fg
