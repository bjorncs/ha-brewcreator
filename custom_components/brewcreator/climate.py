import logging
from collections.abc import Awaitable

from homeassistant.components.climate import (
    FAN_HIGH,
    FAN_LOW,
    FAN_MEDIUM,
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import Ferminator, FerminatorMode
from .coordinator import BrewCreatorDataUpdateCoordinator
from .entity import FerminatorEntity, register_entities

FAN_SPEEDS = {1: FAN_LOW, 2: FAN_MEDIUM, 3: FAN_HIGH, 4: "max"}
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
    register_entities(
        entry,
        async_add_entities,
        Ferminator,
        lambda coordinator, id: [FerminatorConnectClimate(coordinator, id)],
    )


class FerminatorConnectClimate(FerminatorEntity, ClimateEntity):
    def __init__(self, coordinator: BrewCreatorDataUpdateCoordinator, id: str) -> None:
        super().__init__(coordinator, id, "ferminator", "")
        # The main feature of the Ferminator, so it takes the device's name
        self._attr_name = None
        self._attr_supported_features = (
            ClimateEntityFeature.TARGET_TEMPERATURE
            | ClimateEntityFeature.FAN_MODE
            | ClimateEntityFeature.TURN_OFF
            | ClimateEntityFeature.TURN_ON
        )
        self._attr_hvac_modes = [HVACMode.HEAT_COOL, HVACMode.OFF]
        self._attr_fan_modes = list(FAN_SPEEDS.values())
        self._attr_min_temp = 0
        self._attr_max_temp = 50

    @property
    def native_temperature_unit(self) -> str:
        return UnitOfTemperature.CELSIUS

    @property
    def native_current_temperature(self) -> float | None:
        return self._ferminator().actual_temperature

    @property
    def native_target_temperature(self) -> float | None:
        return self._ferminator().target_temperature

    @property
    def hvac_mode(self) -> HVACMode | None:
        is_regulating = self._ferminator().is_regulating
        if is_regulating is None:
            # BrewCreator reports no process (lProcess is null), so the mode is unknown
            return None
        return HVACMode.HEAT_COOL if is_regulating else HVACMode.OFF

    @property
    def hvac_action(self) -> HVACAction | None:
        mode = self._ferminator().mode
        if mode is None:
            _LOGGER.debug(
                "BrewCreator reports no process (lProcess) for %s, so its current action is unknown",
                self._ferminator().name,
            )
            return None
        return MODE_TO_HVAC_ACTION[mode]

    @property
    def fan_mode(self) -> str | None:
        fan_speed = self._ferminator().fan_speed
        return FAN_SPEEDS.get(fan_speed) if fan_speed is not None else None

    async def async_set_temperature(self, **kwargs) -> None:
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is not None:
            await self._async_update(self._ferminator().set_target_temperature(temperature))

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        fan_speed = next(k for k, v in FAN_SPEEDS.items() if v == fan_mode)
        await self._async_update(self._ferminator().set_fan_speed(fan_speed))

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        is_regulating = hvac_mode == HVACMode.HEAT_COOL
        await self._async_update(self._ferminator().set_regulating_temperature(is_regulating))

    async def _async_update(self, update: Awaitable[None]) -> None:
        await self.coordinator.async_send(update, "control_failed", self._ferminator().name)
