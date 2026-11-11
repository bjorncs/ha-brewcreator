import unittest
from unittest.mock import AsyncMock, MagicMock

from homeassistant.components.climate import FAN_HIGH, FAN_LOW, FAN_MEDIUM, HVACAction, HVACMode
from homeassistant.exceptions import HomeAssistantError

from custom_components.brewcreator.api import BrewCreatorError
from custom_components.brewcreator.climate import FerminatorConnectClimate
from tests.factories import coordinator, ferminator


def climate(**extra) -> FerminatorConnectClimate:
    f = ferminator(**extra)
    return FerminatorConnectClimate(coordinator(f), f.id)


class ClimateNamingTestCase(unittest.TestCase):
    def test_takes_the_device_name(self):
        entity = climate()
        self.assertIsNone(entity.name)
        self.assertEqual("ferminator", entity.translation_key)

    def test_fan_modes_are_translation_keys(self):
        entity = climate(fanSpeed=4)
        self.assertEqual([FAN_LOW, FAN_MEDIUM, FAN_HIGH, "max"], entity.fan_modes)
        self.assertEqual("max", entity.fan_mode)


class ClimateModeTestCase(unittest.TestCase):
    def test_mode_and_action_are_unknown_without_process(self):
        entity = climate(lProcess=None)
        self.assertIsNone(entity.hvac_mode)
        self.assertIsNone(entity.hvac_action)

    def test_cooling(self):
        entity = climate(lProcess="Cooling")
        self.assertEqual(HVACMode.HEAT_COOL, entity.hvac_mode)
        self.assertEqual(HVACAction.COOLING, entity.hvac_action)

    def test_ready_is_off(self):
        entity = climate(lProcess="Ready")
        self.assertEqual(HVACMode.OFF, entity.hvac_mode)
        self.assertEqual(HVACAction.OFF, entity.hvac_action)


class ClimateControlTestCase(unittest.IsolatedAsyncioTestCase):
    def climate(self, update: AsyncMock) -> FerminatorConnectClimate:
        entity = climate()
        entity._ferminator()._api = MagicMock(_update_equipment_state=update)
        return entity

    async def test_update_is_sent_then_refreshed(self):
        update = AsyncMock()
        entity = self.climate(update)
        await entity.async_set_temperature(temperature=18.5)
        update.assert_awaited_once_with("f", {"setTemperature": 18.5})
        entity.coordinator.async_refresh.assert_awaited_once()

    async def test_fan_mode_is_sent_as_fan_speed(self):
        update = AsyncMock()
        entity = self.climate(update)
        await entity.async_set_fan_mode(FAN_HIGH)
        update.assert_awaited_once_with("f", {"fanSpeed": 3})

    async def test_rejected_update_raises_home_assistant_error(self):
        entity = self.climate(AsyncMock(side_effect=BrewCreatorError("rejected")))
        with self.assertRaises(HomeAssistantError) as raised:
            await entity.async_set_hvac_mode(HVACMode.OFF)
        self.assertEqual("control_failed", raised.exception.translation_key)
        entity.coordinator.async_refresh.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
