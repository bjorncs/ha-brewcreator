from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from homeassistant.core import Context
from homeassistant.helpers.llm import LLMContext

from custom_components.brewcreator.coordinator import BrewCreatorData
from custom_components.brewcreator.intent import (
    GetFermentationStatusIntentHandler,
    LinkHydrometerIntentHandler,
    SetBatchInfoIntentHandler,
)
from custom_components.brewcreator.llm import async_get_tools
from tests.factories import ferminator, graviator, ha_device, tilt, tool_parameters


class LLMToolsTestCase(unittest.TestCase):
    def setUp(self):
        self.patches = [
            patch("custom_components.brewcreator.intent.dr"),
            patch("custom_components.brewcreator.intent.er"),
            patch("custom_components.brewcreator.intent.async_should_expose"),
            patch("custom_components.brewcreator.llm.intent.async_get"),
        ]
        self.dr, self.er, self.should_expose, self.intent_get = (p.start() for p in self.patches)
        for p in self.patches:
            self.addCleanup(p.stop)
        # Another integration's intent is left alone
        self.intent_get.return_value = [
            SimpleNamespace(intent_type="HassTurnOn"),
            GetFermentationStatusIntentHandler(),
            SetBatchInfoIntentHandler(),
            LinkHydrometerIntentHandler(),
        ]

    def setup(self, *equipment, exposed=None):
        devices = {e.id: ha_device(e.id, name=e.name) for e in equipment}
        self.dr.async_get.return_value.async_get_device_by_identifier.side_effect = (
            lambda identifier, config_entry_id: devices.get(identifier[1])
        )
        self.er.async_entries_for_device.side_effect = lambda registry, device_id: [
            SimpleNamespace(entity_id=f"sensor.{device_id}")
        ]
        exposed = {e.id for e in equipment} if exposed is None else set(exposed)
        self.should_expose.side_effect = lambda hass, assistant, entity_id: (
            assistant == "conversation" and entity_id.removeprefix("sensor.device-") in exposed
        )
        coordinator = MagicMock(data=BrewCreatorData(equipment={e.id: e for e in equipment}))
        self.hass = MagicMock()
        self.hass.config_entries.async_loaded_entries.side_effect = lambda domain: (
            [SimpleNamespace(entry_id="entry", runtime_data=coordinator)] if domain == "brewcreator" else []
        )

    def tools(self, api_id="assist", assistant="conversation"):
        context = LLMContext(
            platform="google_generative_ai_conversation",
            context=Context(),
            language="en",
            assistant=assistant,
            device_id=None,
        )
        return async_get_tools(self.hass, context, api_id)

    def test_offers_the_intents_as_tools_with_an_exposed_ferminator(self):
        self.setup(ferminator("f"), tilt("t"))
        tools = self.tools().tools
        self.assertEqual(
            ["brewcreator__GetFermentationStatus", "brewcreator__SetBatchInfo", "brewcreator__LinkHydrometer"],
            [t.name for t in tools],
        )
        self.assertEqual({"brewcreator"}, {t.integration for t in tools})
        self.assertEqual({None}, {t.extra_slots for t in tools})
        status = tools[0]
        self.assertTrue(status.annotations.read_only)
        self.assertFalse(status.annotations.open_world)
        self.assertEqual("BrewCreatorGetFermentationStatus", status.intent_type)

    def test_only_status_without_an_exposed_ferminator(self):
        self.setup(ferminator("f"), tilt("t"), exposed=["t"])
        self.assertEqual(["brewcreator__GetFermentationStatus"], [t.name for t in self.tools().tools])

    def test_batch_info_without_an_exposed_ferminator_with_an_exposed_graviator(self):
        self.setup(ferminator("f"), graviator("g"), exposed=["g"])
        self.assertEqual(
            ["brewcreator__GetFermentationStatus", "brewcreator__SetBatchInfo"],
            [t.name for t in self.tools().tools],
        )

    def test_nothing_exposed(self):
        self.setup(ferminator("f"), tilt("t"), exposed=[])
        self.assertIsNone(self.tools())

    def test_not_exposed_to_another_assistant(self):
        self.setup(ferminator("f"))
        self.assertIsNone(self.tools(assistant="cloud.alexa"))

    def test_only_for_the_assist_api(self):
        self.setup(ferminator("f"))
        self.assertIsNone(self.tools(api_id="homeassistant"))

    def test_hydrometer_is_required_to_link(self):
        self.assertEqual(["hydrometer"], tool_parameters(LinkHydrometerIntentHandler())["required"])


if __name__ == "__main__":
    unittest.main()
