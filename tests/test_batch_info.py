from datetime import UTC, datetime
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock

from custom_components.brewcreator.api import BATCH_INFO_EQUIPMENT, resolve_links
from custom_components.brewcreator.binary_sensor import BatchStartedEntity
from custom_components.brewcreator.coordinator import BrewCreatorData
from custom_components.brewcreator.entity import register_entities
from custom_components.brewcreator.select import FerminatorHydrometerEntity
from tests.factories import BATCH, coordinator, ferminator, graviator, sensor, tilt


def batch_sensor(key):
    return lambda c, id: sensor(c, id, key, batch_info=True)


BATCH_ENTITIES = {
    "batch_name": batch_sensor("batch_name"),
    "original_gravity": batch_sensor("original_gravity"),
    "batch_started": BatchStartedEntity,
}


def batch_ferminator(connection_state: str = "Connected", **extra):
    return ferminator(connection_state=connection_state, **{**BATCH, **extra})


class BatchOwnerTestCase(unittest.TestCase):
    def owner(self, id, *equipment):
        return coordinator(*equipment).data.equipment[id].batch_owner

    def test_ferminator_owns_its_batch(self):
        f = ferminator()
        self.assertIs(f, self.owner("f", f))

    def test_linked_hydrometers_get_their_ferminators_batch(self):
        f = ferminator(connected=["t", "g"])
        self.assertIs(f, self.owner("t", f, tilt("t")))
        self.assertIs(f, self.owner("g", f, graviator("g")))

    def test_unlinked_graviator_owns_its_batch(self):
        g = graviator("g")
        self.assertIs(g, self.owner("g", ferminator(), g))

    def test_unlinked_tilt_has_no_batch(self):
        self.assertIsNone(self.owner("t", ferminator(), tilt("t")))


class BatchValuesTestCase(unittest.TestCase):
    def entities(self, equipment):
        c = coordinator(equipment)
        return (
            sensor(c, equipment.id, "batch_name", batch_info=True),
            sensor(c, equipment.id, "original_gravity", batch_info=True),
            sensor(c, equipment.id, "final_gravity", batch_info=True),
            BatchStartedEntity(c, equipment.id),
        )

    def test_batch_values(self):
        name, og, fg, started = self.entities(batch_ferminator())
        self.assertEqual("Christmas Miracle", name.native_value)
        self.assertEqual(1.058, og.native_value)
        self.assertEqual(1.015, fg.native_value)
        self.assertTrue(started.is_on)

    def test_fermentation_type_is_a_translation_key(self):
        f = batch_ferminator()
        entity = sensor(coordinator(f), f.id, "fermentation_type", batch_info=True)
        self.assertEqual(["top", "bottom"], entity.options)
        self.assertEqual("top", entity.native_value)
        self.assertEqual("fermentation_type", entity.translation_key)

    def test_without_batch(self):
        name, og, fg, started = self.entities(batch_ferminator(brewName=None, isLoggingData=False))
        self.assertIsNone(name.native_value)
        self.assertIsNone(og.native_value)
        self.assertIsNone(fg.native_value)
        self.assertFalse(started.is_on)

    def test_unset_values_are_unknown(self):
        f = batch_ferminator(og=0, fg=0, owner="")
        _, og, fg, _ = self.entities(f)
        self.assertIsNone(og.native_value)
        self.assertIsNone(fg.native_value)
        self.assertIsNone(f.batch_info.owner)

    def test_brew_date_without_time_zone_is_utc(self):
        f = batch_ferminator(brewDate="2026-11-08T10:00:00")
        self.assertEqual(datetime(2026, 11, 8, 10, tzinfo=UTC), f.batch_info.brew_date)

    def test_values_are_kept_while_the_ferminator_is_offline(self):
        name, og, _, started = self.entities(batch_ferminator("Disconnected"))
        self.assertEqual("Christmas Miracle", name.native_value)
        self.assertEqual(1.058, og.native_value)
        self.assertTrue(started.is_on)

    def test_unlinked_graviator_shows_its_batch_info(self):
        name, og, _, started = self.entities(graviator("g", **{**BATCH, "brewName": "Lager", "og": 1.048}))
        self.assertEqual("Lager", name.native_value)
        self.assertEqual(1.048, og.native_value)
        self.assertTrue(started.is_on)


class BatchAvailabilityTestCase(unittest.TestCase):
    def assert_available(self, available, c, id):
        for name, create in BATCH_ENTITIES.items():
            with self.subTest(name):
                self.assertEqual(available, create(c, id).available)

    def test_available_while_the_ferminator_is_connected(self):
        self.assert_available(True, coordinator(batch_ferminator()), "f")

    def test_stay_available_while_the_ferminator_is_offline(self):
        self.assert_available(True, coordinator(batch_ferminator("Disconnected")), "f")

    def test_unavailable_after_a_failed_refresh(self):
        self.assert_available(False, coordinator(batch_ferminator(), last_update_success=False), "f")

    def test_unavailable_once_the_ferminator_is_gone(self):
        for name, create in BATCH_ENTITIES.items():
            with self.subTest(name):
                c = coordinator(batch_ferminator())
                entity = create(c, "f")
                c.data = BrewCreatorData(equipment={})
                self.assertFalse(entity.available)

    def test_unlinked_graviator_is_available(self):
        self.assert_available(True, coordinator(batch_ferminator(), graviator("g", **BATCH)), "g")

    def test_linked_graviator_is_unavailable_until_unlinked(self):
        f = batch_ferminator(connected=["g"])
        c = coordinator(f, graviator("g", **BATCH))
        self.assert_available(False, c, "g")
        f.json["connectedEquipments"] = []
        resolve_links(c.data.equipment)
        self.assert_available(True, c, "g")

    def test_device_readings_and_hydrometer_link_follow_the_ferminator_connection(self):
        probe = lambda c, id: sensor(c, id, "probe_temp")
        for create in (probe, FerminatorHydrometerEntity):
            with self.subTest(create):
                self.assertFalse(create(coordinator(batch_ferminator("Disconnected")), "f").available)


class RegistrationTestCase(unittest.TestCase):
    def test_created_for_ferminators_and_graviators_only(self):
        c = coordinator()
        entry = SimpleNamespace(runtime_data=c, async_on_unload=MagicMock())
        created = []
        register_entities(
            entry, MagicMock(), BATCH_INFO_EQUIPMENT, lambda coordinator, id: created.append(id) or []
        )
        add_entities = c.async_add_equipment_listener.call_args.args[0]
        add_entities([ferminator(), graviator("g"), tilt("t")])
        self.assertEqual(["f", "g"], created)


if __name__ == "__main__":
    unittest.main()
