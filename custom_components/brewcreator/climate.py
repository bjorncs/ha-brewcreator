import logging

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import BrewCreatorDataUpdateCoordinator
from .api import Ferminator, FerminatorMode
from .entity import FerminatorEntity, register_ferminator_entities

FAN_SPEEDS = {1: "Low", 2: "Medium", 3: "High", 4: "Max"}
MODE_TO_HVAC_ACTION = {
    FerminatorMode.READY: HVACAction.OFF,
    FerminatorMode.COOLING: HVACAction.COOLING,
    FerminatorMode.HEATING: HVACAction.HEATING,
    FerminatorMode.IDLE: HVACAction.IDLE,
}

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[BrewCreatorDataUpdateCoordinator],
    async_add_entities: AddEntitiesCallback,
) -> None:
    register_ferminator_entities(
        entry,
        async_add_entities,
        lambda coordinator, id: [FerminatorConnectClimate(coordinator, id)],
    )


class FerminatorConnectClimate(FerminatorEntity, ClimateEntity):
    def __init__(self, coordinator: BrewCreatorDataUpdateCoordinator, id: str) -> None:
        super().__init__(coordinator, id, "Temperature Control", "")
        self._attr_supported_features = (
            ClimateEntityFeature.TARGET_TEMPERATURE
            | ClimateEntityFeature.FAN_MODE
            | ClimateEntityFeature.TURN_OFF
            | ClimateEntityFeature.TURN_ON
        )
        self._attr_hvac_modes = [HVACMode.HEAT_COOL, HVACMode.OFF]
        self._attr_fan_modes = list(FAN_SPEEDS.values())
        self._attr_temperature_unit = UnitOfTemperature.CELSIUS
        self._attr_min_temp = 0
        self._attr_max_temp = 50

    @property
    def current_temperature(self) -> float | None:
        return self.__ferminator().actual_temperature

    @property
    def target_temperature(self) -> float | None:
        return self.__ferminator().target_temperature

    @property
    def hvac_mode(self) -> HVACMode:
        return (
            HVACMode.HEAT_COOL
            if self.__ferminator().mode != FerminatorMode.READY
            else HVACMode.OFF
        )

    @property
    def hvac_action(self) -> HVACAction:
        return MODE_TO_HVAC_ACTION[self.__ferminator().mode]

    @property
    def fan_mode(self) -> str | None:
        return FAN_SPEEDS.get(self.__ferminator().fan_speed)

    @property
    def available(self) -> bool:
        return self.__ferminator().is_connected

    async def async_turn_on(self) -> None:
        await self.async_set_hvac_mode(HVACMode.HEAT_COOL)

    async def async_turn_off(self) -> None:
        await self.async_set_hvac_mode(HVACMode.OFF)

    async def async_set_temperature(self, **kwargs) -> None:
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is not None:
            await self.__ferminator().set_target_temperature(temperature)

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        fan_speed = next(k for k, v in FAN_SPEEDS.items() if v == fan_mode)
        await self.__ferminator().set_fan_speed(fan_speed)

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        is_regulating = hvac_mode == HVACMode.HEAT_COOL
        await self.__ferminator().set_regulating_temperature(is_regulating)

    def __ferminator(self) -> Ferminator:
        return self.coordinator.data[self._brewcreator_id]

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()
