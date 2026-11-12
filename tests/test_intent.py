import asyncio
import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import Context
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import intent, llm
from homeassistant.util import dt as dt_util

from custom_components.brewcreator.api import BrewCreatorError, Ferminator
from custom_components.brewcreator.intent import (
    INTENT_GET_FERMENTATION_STATUS,
    INTENT_LINK_HYDROMETER,
    INTENT_SET_BATCH_INFO,
    GetFermentationStatusIntentHandler,
    LinkHydrometerIntentHandler,
    SetBatchInfoIntentHandler,
    async_setup_intents,
)
from tests.factories import coordinator as make_coordinator
from tests.factories import ferminator, graviator, ha_device, tilt, tool_parameters

BATCH = {
    "brewName": "Christmas Miracle",
    "owner": "Jane",
    "beerStyle": "Scottish Export",
    "og": 1.058,
    "fg": 1.012,
    "ebc": 34,
    "ibu": 26,
    "volume": 21,
    "fermented": "Top",
    "isLoggingData": True,
}


class IntentTestCase(unittest.TestCase):
    """Runs intents against equipment of one loaded config entry, with registries and exposure mocked."""

    def setUp(self):
        self.patches = [
            patch("custom_components.brewcreator.intent.dr"),
            patch("custom_components.brewcreator.intent.er"),
            patch("custom_components.brewcreator.intent.async_should_expose"),
        ]
        self.dr, self.er, self.should_expose = (p.start() for p in self.patches)
        for p in self.patches:
            self.addCleanup(p.stop)

    def setup(self, *equipment, device_names=None, exposed=None):
        """Create a device per equipment; only those in exposed (all by default) have an exposed entity."""
        device_names = device_names or {}
        devices = {e.id: ha_device(e.id, name=device_names.get(e.id, e.name)) for e in equipment}
        self.dr.async_get.return_value.async_get_device_by_identifier.side_effect = lambda identifier, config_entry_id: (
            devices.get(identifier[1]) if config_entry_id == "entry" else None
        )
        self.er.async_entries_for_device.side_effect = lambda registry, device_id: [
            SimpleNamespace(entity_id=f"sensor.{device_id}")
        ]
        exposed = {e.id for e in equipment} if exposed is None else set(exposed)
        self.should_expose.side_effect = lambda hass, assistant, entity_id: (
            entity_id.removeprefix("sensor.device-") in exposed
        )
        for e in equipment:
            if isinstance(e, Ferminator):
                e._api = MagicMock(_update_equipment_state=AsyncMock())
        self.coordinator = coordinator = make_coordinator(*equipment)
        self.equipment = coordinator.data.equipment
        self.hass = MagicMock()
        self.hass.config_entries.async_loaded_entries.side_effect = lambda domain: (
            [SimpleNamespace(entry_id="entry", runtime_data=coordinator)] if domain == "brewcreator" else []
        )
        self.hass.services.async_call = AsyncMock()
        return devices

    def handle(self, handler, intent_type, **slots):
        intent_obj = intent.Intent(
            self.hass,
            platform="anthropic",
            intent_type=intent_type,
            slots={k: {"value": v} for k, v in slots.items()},
            text_input=None,
            context=Context(),
            language="en",
            assistant="conversation",
        )
        self.assertTrue(handler.async_can_handle(intent_obj))
        return asyncio.run(handler.async_handle(intent_obj))


class FermentationStatusTestCase(IntentTestCase):
    def status(self, **slots):
        return self.handle(GetFermentationStatusIntentHandler(), INTENT_GET_FERMENTATION_STATUS, **slots)

    def test_only_ferminator_needs_no_name(self):
        brew_date = dt_util.utcnow() - timedelta(days=5, hours=1)
        self.setup(
            ferminator(
                "f",
                name="Ferminator",
                connected=["t"],
                setTemperature=19.0,
                brewDate=brew_date.isoformat(),
                **BATCH,
            ),
            tilt("t", name="Tilt Orange", sg=1.020, og=1.058, abv=5.1),
        )
        response = self.status()
        status = response.speech_slots
        self.assertEqual("Christmas Miracle", status["batch"]["name"])
        self.assertEqual(1.058, status["batch"]["original_gravity"])
        self.assertEqual("top", status["batch"]["fermentation_type"])
        self.assertTrue(status["batch"]["started"])
        self.assertEqual(5, status["batch"]["days_since_brew_start"])
        self.assertEqual(19.0, status["ferminator"]["target_temperature_c"])
        self.assertEqual(
            {"name": "Tilt Orange", "specific_gravity": 1.020, "temperature_c": 20, "abv_percent": 5.1},
            status["hydrometer"],
        )
        self.assertIn("Christmas Miracle", response.speech["plain"]["speech"])
        self.assertIn("1.020", response.speech["plain"]["speech"])

    def test_reports_temperature_regulation_and_fan_speed(self):
        for l_process, regulating in (("Ready", False), ("Cooling", True), ("Idle", True)):
            with self.subTest(l_process):
                self.setup(ferminator("f", lProcess=l_process, fanSpeed=2))
                status = self.status().speech_slots["ferminator"]
                self.assertEqual(regulating, status["temperature_regulation"])
                self.assertEqual("medium", status["fan_speed"])

    def test_unknown_mode_leaves_out_temperature_regulation(self):
        self.setup(ferminator("f", lProcess=None))
        status = self.status().speech_slots["ferminator"]
        self.assertNotIn("temperature_regulation", status)
        self.assertNotIn("fan_speed", status)

    def test_hydrometer_by_name_includes_its_ferminators_batch(self):
        self.setup(
            ferminator("f", connected=["t"], **BATCH),
            ferminator("o", name="Other"),
            tilt("t", name="Tilt Orange"),
        )
        status = self.status(name="tilt orange").speech_slots
        self.assertEqual("Tilt Orange", status["hydrometer"]["name"])
        self.assertEqual("Christmas Miracle", status["batch"]["name"])

    def test_device_name_set_by_user_matches(self):
        self.setup(ferminator("f"), ferminator("o"), device_names={"f": "Garage fridge"})
        self.assertEqual("Ferminator", self.status(name="Garage Fridge").speech_slots["ferminator"]["name"])

    def test_several_ferminators_need_a_name(self):
        self.setup(ferminator("f", name="Left"), ferminator("o", name="Right"))
        with self.assertRaises(intent.IntentHandleError) as raised:
            self.status()
        self.assertIn("Left, Right", str(raised.exception))

    def test_part_of_a_single_name_matches(self):
        self.setup(ferminator("f", name="Ferminator Left"), ferminator("o", name="Ferminator Right"))
        self.assertEqual("Ferminator Right", self.status(name="right").speech_slots["ferminator"]["name"])

    def test_ambiguous_name(self):
        self.setup(ferminator("f", name="Ferminator Left"), ferminator("o", name="Ferminator Right"))
        with self.assertRaises(intent.IntentHandleError) as raised:
            self.status(name="ferminator")
        self.assertIn("matches several", str(raised.exception))

    def test_unexposed_equipment_is_hidden(self):
        self.setup(ferminator("f", name="Left"), ferminator("o", name="Right"), exposed={"o"})
        self.assertEqual("Right", self.status().speech_slots["ferminator"]["name"])
        with self.assertRaises(intent.IntentHandleError) as raised:
            self.status(name="Left")
        self.assertNotIn("Left", str(raised.exception).split("are:")[1])

    def test_name_containing_another_devices_name_does_not_match_it(self):
        self.setup(ferminator("f", name="Ferminator"), ferminator("o", name="Ferminator 2"))
        with self.assertRaises(intent.IntentHandleError) as raised:
            self.status(name="ferminator two")
        self.assertIn("no Ferminator or hydrometer named", str(raised.exception))

    def test_reports_the_linked_hydrometer_with_a_reading(self):
        self.setup(
            ferminator("f", connected=["t1", "t2"]),
            tilt("t1", name="Tilt Black", sg=None),
            tilt("t2", name="Tilt Orange"),
        )
        self.assertEqual("Tilt Orange", self.status().speech_slots["hydrometer"]["name"])

    def test_unexposed_linked_hydrometer_is_left_out(self):
        self.setup(ferminator("f", connected=["t"]), tilt("t", name="Tilt Orange"), exposed={"f"})
        self.assertNotIn("hydrometer", self.status().speech_slots)

    def test_unlinked_graviator_includes_its_own_batch(self):
        self.setup(ferminator("f"), graviator("g", name="Graviator", brewName="Lager"))
        status = self.status(name="Graviator").speech_slots
        self.assertEqual("Lager", status["batch"]["name"])
        self.assertNotIn("ferminator", status)

    def test_graviator_with_unexposed_linked_ferminator_has_no_batch(self):
        self.setup(
            ferminator("f", connected=["g"], **BATCH), graviator("g", name="Graviator", brewName="Copy"), exposed={"g"}
        )
        self.assertEqual({"hydrometer"}, self.status(name="Graviator").speech_slots.keys())

    def test_unexposed_linked_ferminator_is_left_out(self):
        self.setup(ferminator("f", connected=["t"], **BATCH), tilt("t", name="Tilt Orange"), exposed={"t"})
        status = self.status(name="Tilt Orange").speech_slots
        self.assertEqual({"hydrometer"}, status.keys())

    def test_speech_without_batch_starts_with_the_name(self):
        self.setup(ferminator("f", name="Ferminator", setTemperature=19.0))
        self.assertEqual("Ferminator at 18.0 °C, target 19.0 °C.", self.status().speech["plain"]["speech"])

    def test_unexposed_with_a_name_hints_at_exposing(self):
        self.setup(ferminator("f"), exposed=())
        with self.assertRaises(intent.IntentHandleError) as raised:
            self.status(name="Ferminator")
        self.assertIn("Expose", str(raised.exception))

    def test_nothing_exposed(self):
        self.setup(ferminator("f"), exposed=())
        with self.assertRaises(intent.IntentHandleError) as raised:
            self.status()
        self.assertIn("Expose", str(raised.exception))


class SetBatchInfoTestCase(IntentTestCase):
    def set_batch_info(self, **slots):
        return self.handle(SetBatchInfoIntentHandler(), INTENT_SET_BATCH_INFO, **slots)

    def test_forwards_given_fields_to_the_action(self):
        devices = self.setup(ferminator("f", **BATCH))
        response = self.set_batch_info(owner="Bob", original_gravity="1.060", started=False)
        self.hass.services.async_call.assert_awaited_once()
        domain, service, data = self.hass.services.async_call.await_args.args
        self.assertEqual(("brewcreator", "set_batch_info"), (domain, service))
        self.assertEqual(
            {"device_id": devices["f"].id, "owner": "Bob", "original_gravity": 1.060, "started": False},
            data,
        )
        self.assertEqual(
            {"owner": "Bob", "original_gravity": 1.060, "started": False}, response.speech_slots["changed"]
        )

    def test_forwards_fields_to_clear(self):
        devices = self.setup(ferminator("f", **BATCH))
        self.set_batch_info(clear=["owner", "fermentation_type"])
        _, _, data = self.hass.services.async_call.await_args.args
        self.assertEqual({"device_id": devices["f"].id, "clear": ["owner", "fermentation_type"]}, data)

    def test_clear_lists_the_clearable_fields(self):
        clear = tool_parameters(SetBatchInfoIntentHandler())["properties"]["clear"]
        self.assertEqual("array", clear["type"])
        self.assertEqual(
            [
                "owner",
                "beer_style",
                "original_gravity",
                "final_gravity",
                "ebc",
                "ibu",
                "volume",
                "fermentation_type",
            ],
            clear["items"]["enum"],
        )

    def test_points_temperature_regulation_to_the_climate_entity(self):
        # An agent once took started=true for turning temperature regulation on
        handler = SetBatchInfoIntentHandler()
        self.assertIn("HassTurnOn", handler.description)
        self.assertIn(
            "Doesn't turn temperature regulation", tool_parameters(handler)["properties"]["started"]["description"]
        )

    def test_needs_a_field(self):
        self.setup(ferminator("f"))
        with self.assertRaises(intent.IntentHandleError):
            self.set_batch_info(name="Ferminator")
        self.hass.services.async_call.assert_not_awaited()

    def test_action_errors_become_intent_errors(self):
        self.setup(ferminator("f", **BATCH))
        self.hass.services.async_call.side_effect = ServiceValidationError("FG must be below OG")
        with self.assertRaises(intent.IntentHandleError) as raised:
            self.set_batch_info(final_gravity=1.070)
        self.assertIn("FG must be below OG", str(raised.exception))

    def test_unlinked_graviator_is_a_target(self):
        devices = self.setup(graviator("g", name="Graviator"))
        response = self.set_batch_info(owner="Bob")
        _, _, data = self.hass.services.async_call.await_args.args
        self.assertEqual({"device_id": devices["g"].id, "owner": "Bob"}, data)
        self.assertEqual("Graviator", response.speech_slots["name"])

    def test_linked_graviator_is_not_the_default(self):
        devices = self.setup(ferminator("f", connected=["g"]), graviator("g", name="Graviator"))
        self.set_batch_info(owner="Bob")
        _, _, data = self.hass.services.async_call.await_args.args
        self.assertEqual(devices["f"].id, data["device_id"])

    def test_linked_graviator_by_name_gets_the_actions_error(self):
        devices = self.setup(ferminator("f", connected=["g"]), graviator("g", name="Graviator"))
        self.hass.services.async_call.side_effect = ServiceValidationError("linked to Ferminator")
        with self.assertRaises(intent.IntentHandleError) as raised:
            self.set_batch_info(name="Graviator", owner="Bob")
        self.assertIn("linked to Ferminator", str(raised.exception))
        _, _, data = self.hass.services.async_call.await_args.args
        self.assertEqual(devices["g"].id, data["device_id"])

    def test_tilts_are_not_targets(self):
        self.setup(ferminator("f", name="Left"), ferminator("o", name="Right"), tilt("t", name="Tilt"))
        with self.assertRaises(intent.IntentHandleError):
            self.set_batch_info(name="Tilt", owner="Bob")


class LinkHydrometerTestCase(IntentTestCase):
    def link(self, **slots):
        return self.handle(LinkHydrometerIntentHandler(), INTENT_LINK_HYDROMETER, **slots)

    def setup_tilts(self):
        self.setup(
            ferminator("f", connected=["t1"]),
            ferminator("o", name="Other", connected=["t3"]),
            tilt("t1", name="Tilt Black"),
            tilt("t2", name="Tilt Orange"),
            tilt("t3", name="Tilt Red"),
        )

    def update(self) -> AsyncMock:
        return self.equipment["f"]._api._update_equipment_state

    def linked_ids(self):
        """Return the hydrometer IDs Ferminator "f" was linked to, which is refreshed afterwards."""
        self.update().assert_awaited_once()
        equipment_id, payload = self.update().await_args.args
        self.assertEqual("f", equipment_id)
        self.coordinator.async_refresh.assert_awaited_once()
        return payload["connectedEquipments"]

    def test_links_by_name_regardless_of_case(self):
        self.setup_tilts()
        response = self.link(name="Ferminator", hydrometer="tilt orange")
        self.assertEqual(["t2"], self.linked_ids())
        self.assertEqual("Linked Tilt Orange to Ferminator.", response.speech["plain"]["speech"])

    def test_links_by_start_of_name(self):
        self.setup_tilts()
        self.link(name="Ferminator", hydrometer="Tilt O")
        self.assertEqual(["t2"], self.linked_ids())

    def test_unlinks(self):
        self.setup_tilts()
        self.link(name="Ferminator", hydrometer="None")
        self.assertEqual([], self.linked_ids())

    def test_rejected_link_raises_intent_error(self):
        self.setup_tilts()
        self.update().side_effect = BrewCreatorError("rejected")
        with self.assertRaises(intent.IntentHandleError) as raised:
            self.link(name="Ferminator", hydrometer="Tilt Orange")
        self.assertIn("rejected", str(raised.exception))
        self.coordinator.async_refresh.assert_not_awaited()

    def test_hydrometer_linked_to_another_ferminator_is_refused(self):
        self.setup_tilts()
        with self.assertRaises(intent.IntentHandleError) as raised:
            self.link(name="Ferminator", hydrometer="Tilt Red")
        self.assertIn("Tilt Black, Tilt Orange", str(raised.exception))
        self.update().assert_not_awaited()

    def test_unexposed_hydrometer_is_not_linkable(self):
        self.setup(
            ferminator("f"),
            tilt("t1", name="Tilt Black"),
            tilt("t2", name="Tilt Orange"),
            exposed={"f", "t1"},
        )
        with self.assertRaises(intent.IntentHandleError) as raised:
            self.link(hydrometer="Tilt Orange")
        self.assertIn("are: Tilt Black", str(raised.exception))
        self.assertNotIn("Tilt Orange", str(raised.exception).split("are:")[1])
        self.update().assert_not_awaited()


class SetupTestCase(unittest.TestCase):
    def test_registers_the_intents(self):
        with patch("custom_components.brewcreator.intent.intent.async_register") as register:
            asyncio.run(async_setup_intents(MagicMock()))
        self.assertEqual(
            {INTENT_GET_FERMENTATION_STATUS, INTENT_SET_BATCH_INFO, INTENT_LINK_HYDROMETER},
            {call.args[1].intent_type for call in register.call_args_list},
        )


class LlmToolTestCase(unittest.TestCase):
    def test_slot_schemas_convert_to_tool_parameters(self):
        for handler in (
            GetFermentationStatusIntentHandler(),
            SetBatchInfoIntentHandler(),
            LinkHydrometerIntentHandler(),
        ):
            with self.subTest(handler.intent_type):
                self.assertEqual(handler.description, llm.IntentTool(handler.intent_type, handler).description)
                parameters = tool_parameters(handler)
                self.assertEqual("object", parameters["type"])
                self.assertIn("name", parameters["properties"])
                for prop in parameters["properties"].values():
                    self.assertIn("description", prop)

    def test_batch_parameters(self):
        parameters = tool_parameters(SetBatchInfoIntentHandler())["properties"]
        self.assertEqual("number", parameters["original_gravity"]["type"])
        self.assertEqual("integer", parameters["ibu"]["type"])
        self.assertEqual("boolean", parameters["started"]["type"])
        self.assertEqual(["top", "bottom"], parameters["fermentation_type"]["enum"])


if __name__ == "__main__":
    unittest.main()
