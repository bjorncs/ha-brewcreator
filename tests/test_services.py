import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
import voluptuous as vol

from custom_components.brewcreator.api import BrewCreatorError
from custom_components.brewcreator.binary_sensor import FerminatorBatchStartedEntity
from custom_components.brewcreator.coordinator import BrewCreatorData
from custom_components.brewcreator.sensor import (
    FerminatorBatchNameEntity,
    FerminatorFinalGravityEntity,
    FerminatorOriginalGravityEntity,
)
from custom_components.brewcreator.services import (
    SET_BATCH_INFO_SCHEMA,
    _async_set_batch_info,
)
from tests.factories import ferminator, ha_device, tilt

BATCH = {
    "brewName": "Christmas Miracle",
    "owner": "bjorncs",
    "brewDate": "2026-11-08T10:00:00+00:00",
    "ebc": 34,
    "ibu": 26,
    "volume": 21,
    "fermented": "Top",
    "og": 1.058,
    "fg": 1.015,
    "beerStyle": "Scottish Export",
    "isLoggingData": True,
}


def batch_ferminator(**batch):
    f = ferminator(**{**BATCH, **batch})
    f._api._update_equipment_state = AsyncMock(return_value=None)
    return f


@patch("custom_components.brewcreator.services.dr")
class SetBatchInfoTestCase(unittest.TestCase):
    def call(self, dr, equipment, device_identifier="f", loaded=True, **data):
        coordinator = MagicMock()
        coordinator.async_refresh = AsyncMock()
        coordinator.data = BrewCreatorData(equipment={e.id: e for e in equipment})
        device = ha_device(device_identifier)
        dr.async_get.return_value.async_get.side_effect = (
            lambda device_id: device if device_id == device.id else None
        )
        hass = MagicMock()
        hass.config_entries.async_loaded_entries.side_effect = lambda domain: (
            [SimpleNamespace(runtime_data=coordinator)]
            if loaded and domain == "brewcreator"
            else []
        )
        call = SimpleNamespace(
            hass=hass,
            data=SET_BATCH_INFO_SCHEMA({"device_id": device.id, **data}),
        )
        asyncio.run(_async_set_batch_info(call))
        return coordinator

    def test_only_given_fields_are_sent(self, dr):
        f = batch_ferminator()
        coordinator = self.call(
            dr,
            [f],
            batch_name="Easter Ale",
            original_gravity=1.050,
            final_gravity=1.010,
            fermentation_type="bottom",
            started=False,
        )
        f._api._update_equipment_state.assert_awaited_once_with(
            "f",
            {
                "brewName": "Easter Ale",
                "og": 1.050,
                "fg": 1.010,
                "fermented": "Bottom",
                "isLoggingData": False,
            },
        )
        coordinator.async_refresh.assert_awaited_once()

    def test_all_fields(self, dr):
        f = batch_ferminator()
        self.call(
            dr,
            [f],
            batch_name="IPA",
            owner="Jane",
            beer_style="American IPA",
            original_gravity="1.062",
            final_gravity=1.012,
            ebc="12",
            ibu=60,
            volume=19,
            fermentation_type="top",
            started=True,
        )
        f._api._update_equipment_state.assert_awaited_once_with(
            "f",
            {
                "brewName": "IPA",
                "owner": "Jane",
                "fg": 1.012,
                "og": 1.062,
                "ebc": 12,
                "ibu": 60,
                "volume": 19,
                "fermented": "Top",
                "beerStyle": "American IPA",
                "isLoggingData": True,
            },
        )

    def test_final_gravity_must_be_below_current_original_gravity(self, dr):
        f = batch_ferminator()
        with self.assertRaises(ServiceValidationError) as raised:
            self.call(dr, [f], final_gravity=1.058)
        self.assertEqual("final_gravity_not_below_original", raised.exception.translation_key)
        f._api._update_equipment_state.assert_not_awaited()

    def test_original_gravity_must_be_above_current_final_gravity(self, dr):
        f = batch_ferminator()
        with self.assertRaises(ServiceValidationError):
            self.call(dr, [f], original_gravity=1.010)

    def test_gravities_given_together_replace_the_current_ones(self, dr):
        f = batch_ferminator()
        self.call(dr, [f], original_gravity=1.012, final_gravity=1.002)
        f._api._update_equipment_state.assert_awaited_once()

    def test_unset_original_gravity_is_not_compared(self, dr):
        f = batch_ferminator(og=0)
        self.call(dr, [f], final_gravity=1.010)
        f._api._update_equipment_state.assert_awaited_once()

    def test_without_batch_info_any_final_gravity_is_accepted(self, dr):
        f = batch_ferminator(brewName=None)
        self.call(dr, [f], final_gravity=1.010)
        f._api._update_equipment_state.assert_awaited_once()

    def test_hydrometer_is_refused(self, dr):
        with self.assertRaises(ServiceValidationError) as raised:
            self.call(dr, [batch_ferminator(), tilt()], device_identifier="t", batch_name="X")
        self.assertEqual("not_a_ferminator", raised.exception.translation_key)

    def test_unknown_device_is_refused(self, dr):
        with self.assertRaises(ServiceValidationError):
            self.call(dr, [], batch_name="X")

    def test_ferminator_of_unloaded_entry_is_refused(self, dr):
        with self.assertRaises(ServiceValidationError):
            self.call(dr, [batch_ferminator()], loaded=False, batch_name="X")

    def test_failed_request_raises(self, dr):
        f = batch_ferminator()
        f._api._update_equipment_state.side_effect = BrewCreatorError("boom")
        with self.assertRaises(HomeAssistantError) as raised:
            self.call(dr, [f], batch_name="X")
        self.assertEqual("update_failed", raised.exception.translation_key)


class SetBatchInfoSchemaTestCase(unittest.TestCase):
    def test_at_least_one_batch_field_is_required(self):
        with self.assertRaises(vol.Invalid):
            SET_BATCH_INFO_SCHEMA({"device_id": "d"})

    def test_out_of_range_values_are_refused(self):
        for field, value in (
            ("original_gravity", 0.97),
            ("final_gravity", 2.0),
            ("ebc", 0),
            ("ibu", 10000),
            ("volume", -1),
            ("batch_name", ""),
            ("owner", "x" * 129),
            ("fermentation_type", "Top"),
        ):
            with self.subTest(field=field), self.assertRaises(vol.Invalid):
                SET_BATCH_INFO_SCHEMA({"device_id": "d", field: value})


class BatchSensorTestCase(unittest.TestCase):
    def entities(self, f):
        coordinator = MagicMock()
        coordinator.data = BrewCreatorData(equipment={f.id: f})
        return (
            FerminatorBatchNameEntity(coordinator, f.id),
            FerminatorOriginalGravityEntity(coordinator, f.id),
            FerminatorFinalGravityEntity(coordinator, f.id),
            FerminatorBatchStartedEntity(coordinator, f.id),
        )

    def test_batch_values(self):
        name, og, fg, started = self.entities(batch_ferminator())
        self.assertEqual("Christmas Miracle", name.native_value)
        self.assertEqual(1.058, og.native_value)
        self.assertEqual(1.015, fg.native_value)
        self.assertTrue(started.is_on)

    def test_without_batch(self):
        name, og, fg, started = self.entities(
            batch_ferminator(brewName=None, isLoggingData=False)
        )
        self.assertIsNone(name.native_value)
        self.assertIsNone(og.native_value)
        self.assertIsNone(fg.native_value)
        self.assertFalse(started.is_on)


if __name__ == "__main__":
    unittest.main()
