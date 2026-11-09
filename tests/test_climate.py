import unittest
from unittest.mock import MagicMock

from homeassistant.components.climate import HVACAction, HVACMode

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


if __name__ == "__main__":
    unittest.main()
