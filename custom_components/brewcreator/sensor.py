"""Sensor entities for Ferminator and hydrometer devices such as Tilt and Graviator."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfTemperature,
    UnitOfVolume,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import BATCH_INFO_EQUIPMENT, FermentationType, Ferminator, Hydrometer
from .coordinator import BrewCreatorDataUpdateCoordinator
from .entity import BatchInfoEntity, BrewCreatorEntity, register_entities


@dataclass(frozen=True, kw_only=True)
class BrewCreatorSensorDescription(SensorEntityDescription):
    """A sensor of a piece of equipment, or of its batch info. The key is the suffix of the entity's unique ID."""

    value_fn: Callable[[Any], Any]
    """Returns the value from the equipment, or for a batch info sensor, from the batch info."""

    available_offline: bool = False
    """Available whenever it has a value, whether or not the equipment is connected."""


_TEMPERATURE = {
    "device_class": SensorDeviceClass.TEMPERATURE,
    "native_unit_of_measurement": UnitOfTemperature.CELSIUS,
    "suggested_display_precision": 1,
    "state_class": SensorStateClass.MEASUREMENT,
}

_LAST_ACTIVITY = {
    "device_class": SensorDeviceClass.TIMESTAMP,
    "entity_category": EntityCategory.DIAGNOSTIC,
    "entity_registry_enabled_default": False,
    "available_offline": True,
}

FERMINATOR_SENSORS = (
    # The climate entity reports the linked hydrometer's temperature instead when it has a reading
    BrewCreatorSensorDescription(
        key="probe_temp",
        translation_key="probe_temperature",
        value_fn=lambda f: f.actual_temperature_builtin_probe,
        **_TEMPERATURE,
    ),
    BrewCreatorSensorDescription(
        key="last_activity",
        translation_key="last_activity",
        value_fn=lambda f: f.last_activity_time,
        **_LAST_ACTIVITY,
    ),
)

HYDROMETER_SENSORS = (
    # Named from its device class
    BrewCreatorSensorDescription(key="temp", value_fn=lambda h: h.actual_temperature, **_TEMPERATURE),
    BrewCreatorSensorDescription(
        key="sg",
        translation_key="specific_gravity",
        suggested_display_precision=3,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda h: h.specific_gravity,
    ),
    # Time of the newest brew log point, which BrewCreator reports as the hydrometer's last activity. Meaningful
    # whether or not the hydrometer is linked. The key is kept from when this was named "Last Activity", so the entity
    # and its history stay.
    BrewCreatorSensorDescription(
        key="last_activity",
        translation_key="last_brew_log_entry",
        value_fn=lambda h: h.last_activity_time,
        **_LAST_ACTIVITY,
    ),
    BrewCreatorSensorDescription(
        key="abv",
        translation_key="abv",
        suggested_display_precision=1,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda h: h.abv,
    ),
)

# Batch info rather than measurements, so they have no state class
BATCH_INFO_SENSORS = (
    BrewCreatorSensorDescription(
        key="brew_date",
        translation_key="brew_date",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda b: b.brew_date,
    ),
    BrewCreatorSensorDescription(key="batch_name", translation_key="batch_name", value_fn=lambda b: b.brew_name),
    BrewCreatorSensorDescription(
        key="original_gravity",
        translation_key="original_gravity",
        suggested_display_precision=3,
        value_fn=lambda b: b.og,
    ),
    BrewCreatorSensorDescription(
        key="final_gravity",
        translation_key="final_gravity",
        suggested_display_precision=3,
        value_fn=lambda b: b.fg,
    ),
    BrewCreatorSensorDescription(key="owner", translation_key="owner", value_fn=lambda b: b.owner),
    BrewCreatorSensorDescription(
        key="ebc", translation_key="ebc", native_unit_of_measurement="EBC", value_fn=lambda b: b.ebc
    ),
    BrewCreatorSensorDescription(
        key="ibu", translation_key="ibu", native_unit_of_measurement="IBU", value_fn=lambda b: b.ibu
    ),
    BrewCreatorSensorDescription(
        key="batch_volume",
        translation_key="batch_volume",
        device_class=SensorDeviceClass.VOLUME_STORAGE,
        native_unit_of_measurement=UnitOfVolume.LITERS,
        value_fn=lambda b: b.volume,
    ),
    BrewCreatorSensorDescription(
        key="fermentation_type",
        translation_key="fermentation_type",
        device_class=SensorDeviceClass.ENUM,
        options=[ft.key for ft in FermentationType],
        value_fn=lambda b: b.fermentation_type.key if b.fermentation_type is not None else None,
    ),
    BrewCreatorSensorDescription(key="beer_style", translation_key="beer_style", value_fn=lambda b: b.beer_style),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
):
    """Set up the hydrometer (Tilt, Graviator) and Ferminator sensors."""
    register_entities(
        entry,
        async_add_entities,
        Ferminator,
        lambda coordinator, id: [BrewCreatorSensor(coordinator, id, d) for d in FERMINATOR_SENSORS],
    )
    register_entities(
        entry,
        async_add_entities,
        BATCH_INFO_EQUIPMENT,
        lambda coordinator, id: [BatchInfoSensor(coordinator, id, d) for d in BATCH_INFO_SENSORS],
    )
    register_entities(
        entry,
        async_add_entities,
        Hydrometer,
        lambda coordinator, id: [BrewCreatorSensor(coordinator, id, d) for d in HYDROMETER_SENSORS],
    )


class BrewCreatorSensor(BrewCreatorEntity, SensorEntity):
    """A sensor of a piece of equipment, available while the equipment is connected unless available_offline."""

    entity_description: BrewCreatorSensorDescription

    def __init__(
        self,
        coordinator: BrewCreatorDataUpdateCoordinator,
        id: str,
        description: BrewCreatorSensorDescription,
    ) -> None:
        super().__init__(coordinator, id, description.translation_key, description.key)
        self.entity_description = description
        if description.available_offline:
            self._requires_connection = False

    @property
    def available(self) -> bool:
        return super().available and (
            not self.entity_description.available_offline or self.native_value is not None
        )

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self._brewcreator_device)


class BatchInfoSensor(BatchInfoEntity, BrewCreatorSensor):
    """A field of the batch info of a Ferminator or an unlinked Graviator, unknown while it has no batch."""

    @property
    def native_value(self) -> Any:
        batch_info = self._brewcreator_device.batch_info
        return self.entity_description.value_fn(batch_info) if batch_info is not None else None
