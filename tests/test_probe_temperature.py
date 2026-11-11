import unittest

from custom_components.brewcreator.climate import FerminatorConnectClimate
from tests.factories import coordinator, ferminator, sensor, tilt


class ProbeTemperatureTestCase(unittest.TestCase):
    def test_reports_probe_while_climate_reports_linked_tilt(self):
        t = tilt(temperature=20.2)
        f = ferminator(connected=[t.id], actualTemperature=21.1)
        c = coordinator(f, t)

        self.assertEqual(21.1, sensor(c, f.id, "probe_temp").native_value)
        self.assertEqual(20.2, FerminatorConnectClimate(c, f.id).current_temperature)

    def test_reports_probe_without_linked_hydrometer(self):
        f = ferminator(actualTemperature=21.1)
        c = coordinator(f)

        self.assertEqual(21.1, sensor(c, f.id, "probe_temp").native_value)
        self.assertEqual(21.1, FerminatorConnectClimate(c, f.id).current_temperature)


if __name__ == "__main__":
    unittest.main()
