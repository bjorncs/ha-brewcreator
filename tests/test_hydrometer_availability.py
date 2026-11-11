import unittest
from unittest.mock import MagicMock

from custom_components.brewcreator.api import Ferminator, Graviator, linked_ferminator
from custom_components.brewcreator.coordinator import BrewCreatorData
from custom_components.brewcreator.sensor import (
    FerminatorLastActivityEntity,
    HydrometerLastBrewLogEntryEntity,
    HydrometerSpecificGravityEntity,
)
from tests import factories
from tests.factories import hydrometer


def ferminator(
    connection_state: str = "Connected", connected=("t",), id: str = "f", is_active: bool | None = None
) -> Ferminator:
    """A Ferminator linked to the hydrometer "t" unless told otherwise."""
    return factories.ferminator(id, connection_state=connection_state, connected=connected, is_active=is_active)


def coordinator(*equipment, last_update_success: bool = True) -> MagicMock:
    coordinator = MagicMock()
    coordinator.data = BrewCreatorData(equipment={e.id: e for e in equipment})
    coordinator.last_update_success = last_update_success
    return coordinator


class HydrometerAvailabilityTestCase(unittest.TestCase):
    def is_available(self, *equipment, last_update_success: bool = True) -> bool:
        return HydrometerSpecificGravityEntity(
            coordinator(*equipment, last_update_success=last_update_success), "t"
        ).available

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
        entity = HydrometerSpecificGravityEntity(coordinator(hydrometer(), ferminator()), "t")
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
        self.assertIs(f, linked_ferminator(t, {"other": other, "f": f, "t": t}))
        self.assertIsNone(linked_ferminator(t, {"other": other, "t": t}))

    def test_ferminator_uses_linked_hydrometer_temperature(self):
        f = ferminator(connected=[])
        f._update_connected_equipment([f, hydrometer(temperature=25)])
        self.assertEqual(18.0, f.actual_temperature)
        f = ferminator()
        f._update_connected_equipment([f, hydrometer(temperature=25)])
        self.assertEqual(25, f.actual_temperature)

    def test_ferminator_uses_builtin_probe_when_sg_is_out_of_range(self):
        f = ferminator()
        f._update_connected_equipment([f, hydrometer(sg=1.31, temperature=25)])
        self.assertEqual(18.0, f.actual_temperature)

    def test_ferminator_uses_builtin_probe_when_temperature_is_out_of_range(self):
        f = ferminator()
        f._update_connected_equipment([f, hydrometer(temperature=150)])
        self.assertEqual(18.0, f.actual_temperature)


class LastActivitySensorTestCase(unittest.TestCase):
    def test_hydrometer_sensor_is_named_last_brew_log_entry_and_keeps_unique_id(self):
        entity = HydrometerLastBrewLogEntryEntity(coordinator(hydrometer(), ferminator()), "t")
        self.assertEqual("last_brew_log_entry", entity.translation_key)
        self.assertEqual("brewcreator_t_last_activity", entity.unique_id)

    def test_hydrometer_sensor_without_brew_log_is_unavailable(self):
        entity = HydrometerLastBrewLogEntryEntity(coordinator(hydrometer(), ferminator()), "t")
        self.assertIsNone(entity.native_value)
        self.assertFalse(entity.available)

    def test_ferminator_sensor_without_last_activity_is_unavailable(self):
        f = ferminator()
        entity = FerminatorLastActivityEntity(coordinator(f), "f")
        self.assertIsNone(entity.native_value)
        self.assertFalse(entity.available)


if __name__ == "__main__":
    unittest.main()
