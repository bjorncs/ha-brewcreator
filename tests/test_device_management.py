"""Adding and removing equipment: the API calls, the coordinator, and the options flow."""

import contextlib
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.data_entry_flow import FlowResultType

from custom_components.brewcreator.api import (
    TILT_IOT_HUB_ID,
    BrewCreatorAPI,
    BrewCreatorError,
    BrewCreatorRequestError,
    Ferminator,
    Graviator,
    GraviatorColor,
    Tilt,
    TiltColor,
    UnsupportedEquipment,
    linkable_hydrometers,
    resolve_links,
)
from custom_components.brewcreator.config_flow import (
    BrewCreatorOptionsFlow,
    free_colors,
    normalize_serial_number,
)
from custom_components.brewcreator.device import device_info
from tests.factories import (
    REGISTERED_TILT,
    bare_coordinator,
    coordinator,
    equipment,
    ferminator,
    graviator,
    ha_device,
    registered_tilt,
    tilt,
)


class TokenStorage:
    async def load_tokens(self):
        return "token", "refresh", datetime.now() + timedelta(hours=1)

    async def save_tokens(self, *tokens):
        pass


def response(status: int, body: dict | None) -> MagicMock:
    resp = MagicMock(status=status, content=body is not None)
    resp.json = AsyncMock(return_value=body)
    return resp


def api_answering(*responses: MagicMock) -> tuple[BrewCreatorAPI, MagicMock]:
    """An API whose requests get the responses in turn, and the session's request mock."""
    session = MagicMock()
    answers = iter(responses)

    @contextlib.asynccontextmanager
    async def request(*args, **kwargs):
        yield next(answers)

    session.request = MagicMock(side_effect=request)
    return BrewCreatorAPI("user", "secret", TokenStorage(), session), session.request


def registered(cls, id: str = "new"):
    """Equipment of the type straight after registering it, with no name."""
    return cls(MagicMock(), equipment(id, cls.__name__, name=None))


class RegisterEquipmentTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_posts_the_serial_number_and_returns_the_typed_equipment(self):
        api, request = api_answering(response(200, {"succeeded": True, "data": REGISTERED_TILT}))
        new = await api.register_equipment(TILT_IOT_HUB_ID)
        self.assertIsInstance(new, Tilt)
        self.assertEqual(REGISTERED_TILT["id"], new.id)
        method, url = request.call_args.args
        self.assertEqual(("POST", "https://api.brewcreator.com/api/v1.0/equipments"), (method, url))
        self.assertEqual({"iotHubId": "Tilt"}, request.call_args.kwargs["json"])

    async def test_unknown_serial_number_raises_its_error_code(self):
        body = {
            "succeeded": False,
            "message": "One or more validation failures have occurred.",
            "errors": ["equipmentDoesNotExistOnIotHub"],
        }
        api, _ = api_answering(response(400, body))
        with self.assertRaises(BrewCreatorRequestError) as raised:
            await api.register_equipment("ZZZZ-ZZZ-ZZZ")
        self.assertEqual(400, raised.exception.status)
        self.assertTrue(raised.exception.has_code("other", "EQUIPMENTDOESNOTEXISTONIOTHUB"))
        self.assertFalse(raised.exception.has_code("other"))

    async def test_server_error_is_not_retried(self):
        # A retry could register a second Tilt
        api, request = api_answering(response(500, {"succeeded": False, "message": "boom"}))
        with self.assertRaises(BrewCreatorRequestError):
            await api.register_equipment(TILT_IOT_HUB_ID)
        request.assert_called_once()

    async def test_unsuccessful_answer_raises(self):
        api, _ = api_answering(response(200, {"succeeded": False, "data": None}))
        with self.assertRaises(BrewCreatorError):
            await api.register_equipment(TILT_IOT_HUB_ID)


class UnregisterEquipmentTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_deletes_the_equipment(self):
        api, request = api_answering(response(200, {"succeeded": True, "data": True}))
        await api.unregister_equipment("t")
        self.assertEqual(("DELETE", "https://api.brewcreator.com/api/v1.0/equipments/t"), request.call_args.args)

    async def test_equipment_already_gone_counts_as_removed_without_retrying(self):
        # BrewCreator answers this with 500 and the code in the message
        api, request = api_answering(response(500, {"succeeded": False, "message": "equipmentDoesNotExist"}))
        await api.unregister_equipment("t")
        request.assert_called_once()

    async def test_other_server_error_raises_without_retrying(self):
        api, request = api_answering(response(500, {"succeeded": False, "message": "boom"}))
        with self.assertRaises(BrewCreatorRequestError):
            await api.unregister_equipment("t")
        request.assert_called_once()

    async def test_other_requests_still_retry_server_errors(self):
        api, request = api_answering(response(500, None), response(200, {"succeeded": True}))
        await api._update_equipment_state("t", {"name": "x"})
        self.assertEqual(2, request.call_count)


class RegisteredEquipmentTestCase(unittest.TestCase):
    """Equipment straight after registering has no name, and a Tilt no colour, until they are set."""

    def test_tilt_without_colour_or_name(self):
        t = registered_tilt()
        self.assertIsNone(t.color)
        self.assertEqual("Tilt", t.name)
        self.assertIsNone(device_info(t)["model"])
        self.assertEqual("Tilt", device_info(t)["name"])

    def test_nameless_hydrometers_are_named_by_their_colour(self):
        self.assertEqual("Tilt Purple", registered_tilt(color="TiltPurple").name)
        self.assertEqual("Graviator Teal", graviator(name=None, color="Teal").name)

    def test_nameless_ferminator_is_named_by_its_type(self):
        self.assertEqual("Ferminator", registered(Ferminator).name)

    @patch("custom_components.brewcreator.api._logged_unknown_colors", set())
    def test_unknown_colour_is_logged_once(self):
        with self.assertLogs("custom_components.brewcreator.api", "WARNING") as logs:
            self.assertIsNone(tilt(color="TiltMagenta").color)
            self.assertIsNone(tilt(color="TiltMagenta").color)
        self.assertEqual(1, len(logs.records))

    def test_nameless_hydrometer_is_linkable(self):
        # A None name used to break sorting, and with it every Ferminator's hydrometer select
        f = ferminator()
        nameless = registered_tilt()
        named = tilt("t2", name="Tilt Orange")
        equipment_by_id = {e.id: e for e in (f, nameless, named)}
        resolve_links(equipment_by_id)
        self.assertEqual([nameless, named], linkable_hydrometers(f, equipment_by_id))


class UnlinkTestCase(unittest.IsolatedAsyncioTestCase):
    def linked(self, *hydrometer_ids: str):
        f = ferminator(connected=list(hydrometer_ids))
        hydrometers = [tilt(i) for i in hydrometer_ids]
        resolve_links({e.id: e for e in (f, *hydrometers)})
        f._update_equipment = AsyncMock()
        return f, hydrometers

    async def test_hydrometer_keeps_the_other_linked_hydrometers(self):
        f, (t, _) = self.linked("t", "g")
        await t.unlink()
        f._update_equipment.assert_awaited_once_with({"connectedEquipments": ["g"]})

    async def test_ferminator_unlinks_all_its_hydrometers(self):
        f, _ = self.linked("t")
        await f.unlink()
        f._update_equipment.assert_awaited_once_with({"connectedEquipments": []})

    async def test_unlinked_equipment_sends_nothing(self):
        f, _ = self.linked()
        await f.unlink()
        await tilt().unlink()
        f._update_equipment.assert_not_awaited()


class SetNameAndColorTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_sets_the_name_and_colour(self):
        t = registered_tilt()
        t._update_equipment = AsyncMock()
        await t.set_name_and_color("Tilt Purple", TiltColor.PURPLE)
        t._update_equipment.assert_awaited_once_with({"name": "Tilt Purple", "color": "TiltPurple"})

    async def test_ferminator_only_gets_a_name(self):
        f = ferminator()
        f._update_equipment = AsyncMock()
        await f.set_name_and_color("Garage")
        f._update_equipment.assert_awaited_once_with({"name": "Garage"})


@patch("custom_components.brewcreator.coordinator.dr")
class UnregisterEquipmentCoordinatorTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.coordinator = bare_coordinator()
        self.coordinator._equipment_listeners = {MagicMock(): {"t", "other"}}
        self.coordinator._api.unregister_equipment = AsyncMock()
        self.coordinator.async_refresh = AsyncMock()
        self.registry = MagicMock()
        self.equipment = tilt("t")
        self.equipment.unlink = AsyncMock()

    async def unregister(self, dr, *, device=None):
        dr.async_get.return_value = self.registry
        self.registry.async_get_device_by_identifier.return_value = device
        await self.coordinator.async_unregister_equipment(self.equipment)

    async def test_unlinks_then_removes_it_from_brewcreator_and_home_assistant(self, dr):
        await self.unregister(dr, device=ha_device("t"))
        self.equipment.unlink.assert_awaited_once()
        self.coordinator._api.unregister_equipment.assert_awaited_once_with("t")
        self.registry.async_remove_device.assert_called_once_with("device-t")
        # Forgotten, so it is added again if it comes back
        self.assertEqual([{"other"}], list(self.coordinator._equipment_listeners.values()))
        self.coordinator.async_refresh.assert_awaited_once()

    async def test_failing_unlink_still_removes_it(self, dr):
        self.equipment.unlink.side_effect = BrewCreatorError("offline")
        with self.assertLogs("custom_components.brewcreator.coordinator", "WARNING"):
            await self.unregister(dr)
        self.coordinator._api.unregister_equipment.assert_awaited_once_with("t")

    async def test_failing_removal_keeps_the_device(self, dr):
        self.coordinator._api.unregister_equipment.side_effect = BrewCreatorError("boom")
        with self.assertRaises(BrewCreatorError):
            await self.unregister(dr, device=ha_device("t"))
        self.registry.async_remove_device.assert_not_called()


class SerialNumberTestCase(unittest.TestCase):
    def test_normalized_with_dashes_keeping_the_case(self):
        for value in ("0723-si7-4yk", "0723si74yk", " 0723 si7 4yk ", "0723-si74yk"):
            self.assertEqual("0723-si7-4yk", normalize_serial_number(value), value)
        self.assertEqual("AB12-CD3-EF4", normalize_serial_number("AB12CD3EF4"))

    def test_anything_else_is_rejected(self):
        for value in ("", "Tilt", "0723-si7-4y", "0723-si7-4ykk", "0723_si7_4yk", "ÆØÅ1-si7-4yk"):
            self.assertIsNone(normalize_serial_number(value), value)


class FreeColorsTestCase(unittest.TestCase):
    def test_colours_no_hydrometer_of_the_type_has(self):
        equipment_by_id = {
            e.id: e for e in (tilt("t1", color="TiltOrange"), tilt("t2", color="TiltBlack"), graviator(color="Red"))
        }
        self.assertEqual(
            [c for c in TiltColor if c not in (TiltColor.ORANGE, TiltColor.BLACK)], free_colors(Tilt, equipment_by_id)
        )
        self.assertEqual(list(GraviatorColor)[1:], free_colors(Graviator, equipment_by_id))


class OptionsFlowTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.existing = [ferminator(), tilt("t", color="TiltOrange", name="Tilt Orange")]
        self.coordinator = coordinator(*self.existing)
        self.coordinator.async_unregister_equipment = AsyncMock()
        self.entry = SimpleNamespace(state=ConfigEntryState.LOADED, runtime_data=self.coordinator)
        self.flow = BrewCreatorOptionsFlow()
        self.flow.hass = MagicMock()
        self.flow.hass.config_entries.async_get_known_entry.return_value = self.entry
        self.flow.handler = "entry"
        self.flow.flow_id = "flow"

    def registering(self, new):
        new._update_equipment = AsyncMock()
        self.coordinator.async_register_equipment = AsyncMock(return_value=new)
        return new

    async def test_menu(self):
        result = await self.flow.async_step_init()
        self.assertEqual(FlowResultType.MENU, result["type"])
        self.assertEqual(["add_device", "remove_device"], result["menu_options"])
        result = await self.flow.async_step_add_device()
        self.assertEqual(["add_by_serial_number", "add_tilt"], result["menu_options"])

    async def test_not_loaded_entry_aborts(self):
        self.entry.state = ConfigEntryState.SETUP_RETRY
        self.assertEqual("not_loaded", (await self.flow.async_step_init())["reason"])

    async def test_tilt_offers_only_free_colours(self):
        result = await self.flow.async_step_add_tilt()
        self.assertEqual(FlowResultType.FORM, result["type"])
        options = result["data_schema"].schema["color"].config["options"]
        self.assertNotIn("orange", options)
        self.assertIn("purple", options)

    async def test_adding_a_tilt(self):
        new = self.registering(registered_tilt())
        result = await self.flow.async_step_add_tilt({"color": "purple"})
        self.coordinator.async_register_equipment.assert_awaited_once_with("Tilt")
        new._update_equipment.assert_awaited_once_with({"name": "Tilt Purple", "color": "TiltPurple"})
        self.coordinator.async_refresh.assert_awaited_once()
        self.assertEqual(FlowResultType.ABORT, result["type"])
        self.assertEqual("device_added", result["reason"])
        self.assertEqual({"name": "Tilt Purple"}, result["description_placeholders"])

    async def test_adding_a_ferminator_by_serial_number_with_a_name(self):
        new = self.registering(registered(Ferminator))
        result = await self.flow.async_step_add_by_serial_number({"serial_number": "ab12cd3ef4", "name": "  Garage  "})
        self.coordinator.async_register_equipment.assert_awaited_once_with("ab12-cd3-ef4")
        new._update_equipment.assert_awaited_once_with({"name": "Garage"})
        self.assertEqual("device_added", result["reason"])

    async def test_adding_a_graviator_asks_for_its_colour(self):
        new = self.registering(registered(Graviator))
        result = await self.flow.async_step_add_by_serial_number({"serial_number": "ab12-cd3-ef4"})
        self.assertEqual("graviator_color", result["step_id"])
        new._update_equipment.assert_not_awaited()

        result = await self.flow.async_step_graviator_color({"color": "teal"})
        new._update_equipment.assert_awaited_once_with({"name": "Graviator Teal", "color": "Teal"})
        self.assertEqual("device_added", result["reason"])
        # Finished, so closing the flow removes nothing
        self.flow.async_remove()
        self.flow.hass.async_create_task.assert_not_called()

    async def test_closing_the_flow_before_the_graviator_has_a_colour_removes_it_again(self):
        new = self.registering(registered(Graviator))
        await self.flow.async_step_add_by_serial_number({"serial_number": "ab12-cd3-ef4"})
        self.flow.async_remove()
        await self.flow.hass.async_create_task.call_args.args[0]
        self.coordinator.async_unregister_equipment.assert_awaited_once_with(new)

    async def test_unfinished_graviator_is_removed_even_once_the_entry_is_gone(self):
        new = self.registering(registered(Graviator))
        await self.flow.async_step_add_by_serial_number({"serial_number": "ab12-cd3-ef4"})
        self.flow.hass.config_entries.async_get_known_entry.side_effect = KeyError("entry")
        self.flow.async_remove()
        await self.flow.hass.async_create_task.call_args.args[0]
        self.coordinator.async_unregister_equipment.assert_awaited_once_with(new)

    async def test_failing_background_removal_is_logged(self):
        self.registering(registered(Graviator))
        await self.flow.async_step_add_by_serial_number({"serial_number": "ab12-cd3-ef4"})
        self.coordinator.async_unregister_equipment.side_effect = RuntimeError("closed")
        self.flow.async_remove()
        with self.assertLogs("custom_components.brewcreator.config_flow", "ERROR"):
            await self.flow.hass.async_create_task.call_args.args[0]

    async def test_graviator_colour_taken_meanwhile_asks_again(self):
        new = self.registering(registered(Graviator))
        await self.flow.async_step_add_by_serial_number({"serial_number": "ab12-cd3-ef4"})
        taken = graviator("g2", color="Teal")
        self.coordinator.data.equipment[taken.id] = taken
        result = await self.flow.async_step_graviator_color({"color": "teal"})
        self.assertEqual({"color": "color_taken"}, result["errors"])
        self.assertNotIn("teal", result["data_schema"].schema["color"].config["options"])
        new._update_equipment.assert_not_awaited()

    async def test_no_free_graviator_colour_removes_it_again(self):
        for i, color in enumerate(GraviatorColor):
            g = graviator(f"g{i}", color=color.value)
            self.coordinator.data.equipment[g.id] = g
        new = self.registering(registered(Graviator))
        result = await self.flow.async_step_add_by_serial_number({"serial_number": "ab12-cd3-ef4"})
        self.assertEqual("no_free_graviator_color", result["reason"])
        self.coordinator.async_unregister_equipment.assert_awaited_once_with(new)

    async def test_unsupported_type_is_removed_again(self):
        new = self.registering(UnsupportedEquipment(MagicMock(), equipment("new", "Plaato", name=None)))
        result = await self.flow.async_step_add_by_serial_number({"serial_number": "ab12-cd3-ef4"})
        self.assertEqual({"base": "unsupported_type"}, result["errors"])
        self.assertEqual({"type": "Plaato"}, result["description_placeholders"])
        self.coordinator.async_unregister_equipment.assert_awaited_once_with(new)

    async def test_invalid_serial_number_is_not_sent(self):
        self.coordinator.async_register_equipment = AsyncMock()
        result = await self.flow.async_step_add_by_serial_number({"serial_number": "Tilt"})
        self.assertEqual({"serial_number": "invalid_serial_number"}, result["errors"])
        self.coordinator.async_register_equipment.assert_not_awaited()

    async def test_serial_number_already_in_the_account(self):
        self.coordinator.async_register_equipment = AsyncMock()
        self.existing[0].json["iotHubBrewEquipmentId"] = "0723-si7-4yk"
        result = await self.flow.async_step_add_by_serial_number({"serial_number": "0723-SI7-4YK"})
        self.assertEqual({"serial_number": "already_in_account"}, result["errors"])
        self.coordinator.async_register_equipment.assert_not_awaited()

    async def test_registration_errors(self):
        cases = [
            (BrewCreatorRequestError("400", 400, ["equipmentDoesNotExistOnIotHub"]), "serial_number_not_found", {}),
            (BrewCreatorRequestError("400", 400, ["EquipmentHaveAlreadyRegistered"]), "already_registered", {}),
            (BrewCreatorError("timeout"), "cannot_add", {"error": "timeout"}),
        ]
        for error, key, placeholders in cases:
            with self.subTest(key=key):
                self.coordinator.async_register_equipment = AsyncMock(side_effect=error)
                result = await self.flow.async_step_add_by_serial_number({"serial_number": "zzzz-zzz-zzz"})
                self.assertEqual(FlowResultType.FORM, result["type"])
                self.assertEqual({"base": key}, result["errors"])
                self.assertEqual(placeholders, result["description_placeholders"])

    async def test_tilt_colour_taken_meanwhile_is_not_registered(self):
        self.coordinator.async_register_equipment = AsyncMock()
        taken = tilt("t2", color="TiltPurple")
        self.coordinator.data.equipment[taken.id] = taken
        result = await self.flow.async_step_add_tilt({"color": "purple"})
        self.assertEqual({"color": "color_taken"}, result["errors"])
        self.coordinator.async_register_equipment.assert_not_awaited()

    async def test_failing_to_name_it_removes_it_again(self):
        new = self.registering(registered_tilt())
        new._update_equipment.side_effect = BrewCreatorError("boom")
        result = await self.flow.async_step_add_tilt({"color": "purple"})
        self.assertEqual("cannot_add", result["reason"])
        self.assertEqual({"error": "boom"}, result["description_placeholders"])
        self.coordinator.async_unregister_equipment.assert_awaited_once_with(new)

    async def test_no_free_tilt_colour_aborts(self):
        for i, color in enumerate(TiltColor):
            t = tilt(f"t{i}", color=color.value)
            self.coordinator.data.equipment[t.id] = t
        self.assertEqual("no_free_tilt_color", (await self.flow.async_step_add_tilt())["reason"])

    async def test_removing_a_device_after_confirming(self):
        result = await self.flow.async_step_remove_device()
        options = result["data_schema"].schema["equipment"].config["options"]
        self.assertEqual(["Ferminator (Ferminator)", "Tilt Orange (Tilt)"], [o["label"] for o in options])

        result = await self.flow.async_step_remove_device({"equipment": "t"})
        self.assertEqual("remove_confirm", result["step_id"])
        self.assertEqual("Tilt Orange", result["description_placeholders"]["name"])
        self.coordinator.async_unregister_equipment.assert_not_awaited()

        result = await self.flow.async_step_remove_confirm({})
        self.coordinator.async_unregister_equipment.assert_awaited_once_with(self.existing[1])
        self.assertEqual("device_removed", result["reason"])

    async def test_devices_sharing_a_name_are_told_apart(self):
        # Registered close together, so their time-ordered IDs (UUIDv7) start alike
        for id, color in (
            ("01a209b1-cbe6-75ea-84be-f8388a39f308", "TiltBlack"),
            ("01a209b1-ffff-7771-b846-7a5d9744b421", "TiltRed"),
        ):
            twin = tilt(id, color=color, name="Fermenter")
            self.coordinator.data.equipment[twin.id] = twin
        result = await self.flow.async_step_remove_device()
        labels = [o["label"] for o in result["data_schema"].schema["equipment"].config["options"]]
        self.assertEqual(
            [
                "Fermenter (Tilt) (8a39f308)",
                "Fermenter (Tilt) (9744b421)",
                "Ferminator (Ferminator)",
                "Tilt Orange (Tilt)",
            ],
            labels,
        )

    async def test_removing_nothing_asks_again(self):
        # The field is optional, so the frontend doesn't preselect the first device
        result = await self.flow.async_step_remove_device({})
        self.assertEqual("remove_device", result["step_id"])
        self.assertEqual({"equipment": "select_device"}, result["errors"])

    async def test_device_gone_before_confirming(self):
        await self.flow.async_step_remove_device({"equipment": "t"})
        del self.coordinator.data.equipment["t"]
        self.assertEqual("already_removed", (await self.flow.async_step_remove_confirm({}))["reason"])
        self.coordinator.async_unregister_equipment.assert_not_awaited()

    async def test_failing_removal_shows_the_error(self):
        self.coordinator.async_unregister_equipment.side_effect = BrewCreatorError("boom")
        await self.flow.async_step_remove_device({"equipment": "t"})
        result = await self.flow.async_step_remove_confirm({})
        self.assertEqual({"base": "cannot_remove"}, result["errors"])
        self.assertEqual("boom", result["description_placeholders"]["error"])


if __name__ == "__main__":
    unittest.main()
