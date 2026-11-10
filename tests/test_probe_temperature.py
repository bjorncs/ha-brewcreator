import unittest
from unittest.mock import MagicMock

from custom_components.brewcreator.climate import FerminatorConnectClimate
from custom_components.brewcreator.coordinator import BrewCreatorData
from custom_components.brewcreator.sensor import FerminatorProbeTemperatureEntity
from tests.factories import ferminator, tilt


def coordinator_with(*equipment) -> MagicMock:
    coordinator = MagicMock()
    coordinator.data = BrewCreatorData(equipment={e.id: e for e in equipment})
    return coordinator


class ProbeTemperatureTestCase(unittest.TestCase):
    def test_reports_probe_while_climate_reports_linked_tilt(self):
        t = tilt(temperature=20.2)
        f = ferminator(connected=[t.id], actualTemperature=21.1)
        f._update_connected_equipment([t])
        coordinator = coordinator_with(f, t)

        self.assertEqual(21.1, FerminatorProbeTemperatureEntity(coordinator, f.id).native_value)
        self.assertEqual(20.2, FerminatorConnectClimate(coordinator, f.id).current_temperature)

    def test_reports_probe_without_linked_hydrometer(self):
        f = ferminator(actualTemperature=21.1)
        coordinator = coordinator_with(f)

        self.assertEqual(21.1, FerminatorProbeTemperatureEntity(coordinator, f.id).native_value)
        self.assertEqual(21.1, FerminatorConnectClimate(coordinator, f.id).current_temperature)


if __name__ == "__main__":
    unittest.main()
