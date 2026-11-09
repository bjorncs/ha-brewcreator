import unittest
from unittest.mock import AsyncMock, MagicMock

from homeassistant.components.climate import HVACAction, HVACMode
from homeassistant.exceptions import HomeAssistantError

from custom_components.brewcreator.api import BrewCreatorError
from custom_components.brewcreator.climate import FerminatorConnectClimate
from custom_components.brewcreator.coordinator import BrewCreatorData
from tests.factories import ferminator


def climate(**extra) -> FerminatorConnectClimate:
    coordinator = MagicMock()
    f = ferminator(**extra)
    coordinator.data = BrewCreatorData(equipment={f.id: f})
    return FerminatorConnectClimate(coordinator, f.id)


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
        entity.coordinator.async_refresh = AsyncMock()
        return entity

    async def test_update_is_sent_then_refreshed(self):
        update = AsyncMock()
        entity = self.climate(update)
        await entity.async_set_temperature(temperature=18.5)
        update.assert_awaited_once_with("f", {"setTemperature": 18.5})
        entity.coordinator.async_refresh.assert_awaited_once()

    async def test_rejected_update_raises_home_assistant_error(self):
        entity = self.climate(AsyncMock(side_effect=BrewCreatorError("rejected")))
        with self.assertRaises(HomeAssistantError) as raised:
            await entity.async_set_hvac_mode(HVACMode.OFF)
        self.assertEqual("control_failed", raised.exception.translation_key)
        entity.coordinator.async_refresh.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
