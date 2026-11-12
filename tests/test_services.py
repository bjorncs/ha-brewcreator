import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import probatio
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from custom_components.brewcreator.api import BatchField, BrewCreatorError
from custom_components.brewcreator.services import (
    BATCH_FIELDS,
    SET_BATCH_INFO_SCHEMA,
    _async_set_batch_info,
)
from tests.factories import BATCH, ferminator, graviator, ha_device, tilt
from tests.factories import coordinator as make_coordinator


def batch_ferminator(**batch):
    f = ferminator(**{**BATCH, **batch})
    f._api._update_equipment_state = AsyncMock(return_value=None)
    return f


@patch("custom_components.brewcreator.services.dr")
class SetBatchInfoTestCase(unittest.TestCase):
    def call(self, dr, equipment, device_identifier="f", loaded=True, **data):
        coordinator = make_coordinator(*equipment)
        device = ha_device(device_identifier)
        dr.async_get.return_value.async_get.side_effect = lambda device_id: device if device_id == device.id else None
        hass = MagicMock()
        hass.config_entries.async_loaded_entries.side_effect = lambda domain: (
            [SimpleNamespace(runtime_data=coordinator)] if loaded and domain == "brewcreator" else []
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

    def test_cleared_fields_are_sent_as_brewcreators_unset_values(self, dr):
        # BrewCreator ignores null, so a field is cleared with the value it reports for one that isn't set
        f = batch_ferminator()
        self.call(
            dr,
            [f],
            batch_name="Easter Ale",
            clear=["owner", "beer_style", "ebc", "ibu", "volume", "fermentation_type"],
        )
        f._api._update_equipment_state.assert_awaited_once_with(
            "f",
            {
                "brewName": "Easter Ale",
                "owner": "",
                "beerStyle": "",
                "ebc": 0,
                "ibu": 0,
                "volume": 0,
                "fermented": "None",
            },
        )

    def test_clearing_alone_is_enough(self, dr):
        f = batch_ferminator()
        self.call(dr, [f], clear="owner")
        f._api._update_equipment_state.assert_awaited_once_with("f", {"owner": ""})

    def test_a_field_cannot_be_both_given_and_cleared(self, dr):
        f = batch_ferminator()
        with self.assertRaises(ServiceValidationError) as raised:
            self.call(dr, [f], ebc=12, clear=["owner", "ebc"])
        self.assertEqual("set_and_cleared", raised.exception.translation_key)
        self.assertEqual({"fields": "ebc"}, raised.exception.translation_placeholders)
        f._api._update_equipment_state.assert_not_awaited()

    def test_a_field_that_cannot_be_cleared_is_refused_before_sending(self, dr):
        # The action can't ask for this, as only CLEARABLE_FIELDS can be cleared
        f = batch_ferminator()
        with self.assertRaises(ValueError):
            asyncio.run(f.set_batch_info({BatchField.STARTED: None}))
        f._api._update_equipment_state.assert_not_awaited()

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
        self.assertEqual("no_batch_info", raised.exception.translation_key)

    def test_unlinked_graviator_is_accepted(self, dr):
        g = graviator("g", **BATCH)
        g._api._update_equipment_state = AsyncMock(return_value=None)
        coordinator = self.call(dr, [batch_ferminator(), g], device_identifier="g", batch_name="Lager")
        g._api._update_equipment_state.assert_awaited_once_with("g", {"brewName": "Lager"})
        coordinator.async_refresh.assert_awaited_once()

    def test_linked_graviator_is_refused(self, dr):
        g = graviator("g", **BATCH)
        g._api._update_equipment_state = AsyncMock(return_value=None)
        with self.assertRaises(ServiceValidationError) as raised:
            self.call(dr, [batch_ferminator(connected=["g"]), g], device_identifier="g", batch_name="X")
        self.assertEqual("linked_to_ferminator", raised.exception.translation_key)
        g._api._update_equipment_state.assert_not_awaited()

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
    def test_batch_fields_are_the_schemas_fields(self):
        schema_keys = {str(key) for key in SET_BATCH_INFO_SCHEMA.validators[0].schema}
        self.assertEqual(schema_keys - {"device_id"}, set(BATCH_FIELDS))

    def test_at_least_one_batch_field_is_required(self):
        with self.assertRaises(probatio.Invalid):
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
            with self.subTest(field=field), self.assertRaises(probatio.Invalid):
                SET_BATCH_INFO_SCHEMA({"device_id": "d", field: value})

    def test_only_clearable_fields_can_be_cleared(self):
        for field in ("batch_name", "original_gravity", "final_gravity", "started", "device_id"):
            with self.subTest(field=field), self.assertRaises(probatio.Invalid):
                SET_BATCH_INFO_SCHEMA({"device_id": "d", "clear": [field]})

    def test_clear_needs_a_field(self):
        with self.assertRaises(probatio.Invalid):
            SET_BATCH_INFO_SCHEMA({"device_id": "d", "clear": []})


if __name__ == "__main__":
    unittest.main()
