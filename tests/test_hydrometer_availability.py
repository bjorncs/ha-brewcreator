import unittest

from custom_components.brewcreator.api import Ferminator, Graviator
from custom_components.brewcreator.coordinator import BrewCreatorData
from tests import factories
from tests.factories import coordinator, hydrometer, sensor


def ferminator(
    connection_state: str = "Connected", connected=("t",), id: str = "f", is_active: bool | None = None
) -> Ferminator:
    """A Ferminator linked to the hydrometer "t" unless told otherwise."""
    return factories.ferminator(id, connection_state=connection_state, connected=connected, is_active=is_active)


class HydrometerAvailabilityTestCase(unittest.TestCase):
    def is_available(self, *equipment, last_update_success: bool = True) -> bool:
        return sensor(coordinator(*equipment, last_update_success=last_update_success), "t", "sg").available

    def test_tilt_linked_to_connected_ferminator_is_available(self):
        self.assertTrue(self.is_available(hydrometer(), ferminator()))

    def test_tilt_linked_to_disconnected_ferminator_is_unavailable(self):
        self.assertFalse(self.is_available(hydrometer(), ferminator("Disconnected")))

    def test_tilt_stays_available_while_its_ferminator_briefly_reconnects_to_the_cloud(self):
        self.assertTrue(self.is_available(hydrometer(), ferminator(is_active=False)))

    def test_tilt_linked_to_ferminator_that_just_reconnected_is_available(self):
        self.assertTrue(self.is_available(hydrometer(), ferminator("Disconnected", is_active=True)))

    def test_unlinked_tilt_is_unavailable(self):
        self.assertFalse(self.is_available(hydrometer(), ferminator(connected=[])))

    def test_tilt_without_ferminator_is_unavailable(self):
        self.assertFalse(self.is_available(hydrometer()))

    def test_tilt_without_data_is_unavailable(self):
        self.assertFalse(
            self.is_available(hydrometer(sg=None, temperature=None), ferminator())
        )

    def test_tilt_with_only_temperature_is_available(self):
        self.assertTrue(self.is_available(hydrometer(sg=0), ferminator()))

    def test_tilt_equipment_id_is_ignored(self):
        tilt = hydrometer()
        tilt.json["equipmentId"] = "f"
        self.assertFalse(self.is_available(tilt, ferminator(connected=[])))

    def test_graviator_linked_to_connected_ferminator_is_available(self):
        self.assertTrue(self.is_available(hydrometer(Graviator, "t"), ferminator()))

    def test_graviator_linked_to_disconnected_ferminator_is_unavailable(self):
        self.assertFalse(
            self.is_available(hydrometer(Graviator, "t"), ferminator("Disconnected"))
        )

    def test_unlinked_graviator_with_valid_reading_is_available(self):
        self.assertTrue(
            self.is_available(hydrometer(Graviator, "t"), ferminator(connected=[]))
        )

    def test_unlinked_graviator_without_valid_reading_is_unavailable(self):
        self.assertFalse(self.is_available(hydrometer(Graviator, "t", sg=0)))

    def test_unavailable_when_equipment_is_gone(self):
        entity = sensor(coordinator(hydrometer(), ferminator()), "t", "sg")
        entity.coordinator.data = BrewCreatorData(equipment={})
        self.assertFalse(entity.available)

    def test_unavailable_when_last_update_failed(self):
        self.assertFalse(
            self.is_available(hydrometer(), ferminator(), last_update_success=False)
        )


class HydrometerLinkTestCase(unittest.TestCase):
    def test_linked_ferminator_is_the_one_listing_the_hydrometer(self):
        f = ferminator()
        other = ferminator(id="other", connected=[])
        t = hydrometer()
        coordinator(other, f, t)
        self.assertIs(f, t.linked_ferminator)
        coordinator(other, t)
        self.assertIsNone(t.linked_ferminator)

    def test_ferminator_uses_linked_hydrometer_temperature(self):
        f = ferminator(connected=[])
        coordinator(f, hydrometer(temperature=25))
        self.assertEqual(18.0, f.actual_temperature)
        f = ferminator()
        coordinator(f, hydrometer(temperature=25))
        self.assertEqual(25, f.actual_temperature)

    def test_ferminator_uses_builtin_probe_when_sg_is_out_of_range(self):
        f = ferminator()
        coordinator(f, hydrometer(sg=1.31, temperature=25))
        self.assertEqual(18.0, f.actual_temperature)

    def test_ferminator_uses_builtin_probe_when_temperature_is_out_of_range(self):
        f = ferminator()
        coordinator(f, hydrometer(temperature=150))
        self.assertEqual(18.0, f.actual_temperature)


class LastActivitySensorTestCase(unittest.TestCase):
    def test_hydrometer_sensor_is_named_last_brew_log_entry_and_keeps_unique_id(self):
        entity = sensor(coordinator(hydrometer(), ferminator()), "t", "last_activity")
        self.assertEqual("last_brew_log_entry", entity.translation_key)
        self.assertEqual("brewcreator_t_last_activity", entity.unique_id)

    def test_hydrometer_sensor_without_brew_log_is_unavailable(self):
        entity = sensor(coordinator(hydrometer(), ferminator()), "t", "last_activity")
        self.assertIsNone(entity.native_value)
        self.assertFalse(entity.available)

    def test_ferminator_sensor_without_last_activity_is_unavailable(self):
        f = ferminator()
        entity = sensor(coordinator(f), "f", "last_activity")
        self.assertIsNone(entity.native_value)
        self.assertFalse(entity.available)


if __name__ == "__main__":
    unittest.main()
