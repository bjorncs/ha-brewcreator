import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.helpers.device_registry import DeviceEntryDisabler

from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.brewcreator import async_remove_config_entry_device
from custom_components.brewcreator.api import BrewCreatorEquipment, BrewCreatorError
from custom_components.brewcreator.coordinator import (
    BrewCreatorData,
    BrewCreatorDataUpdateCoordinator,
)
from custom_components.brewcreator.entity import register_hydrometer_entities
from custom_components.brewcreator.sensor import HydrometerSpecificGravityEntity
from tests.factories import DEVICETWIN_MESSAGE, ferminator, tilt, unsupported
from tests.factories import ha_device as device


def make_coordinator() -> BrewCreatorDataUpdateCoordinator:
    coordinator = object.__new__(BrewCreatorDataUpdateCoordinator)
    coordinator.hass = MagicMock()
    coordinator.config_entry = SimpleNamespace(entry_id="entry")
    coordinator.data = None
    coordinator.last_update_success = True
    coordinator._equipment_listeners = {}
    coordinator._unsupported_issue_ids = set()
    return coordinator


INTEGRATION = DeviceEntryDisabler.INTEGRATION


@patch("custom_components.brewcreator.coordinator.ir", MagicMock())
@patch("custom_components.brewcreator.coordinator.dr")
class DeviceSyncTestCase(unittest.TestCase):
    def setUp(self):
        self.coordinator = make_coordinator()
        self.devices: list[SimpleNamespace] = []
        self.registry = MagicMock()
        self.registry.async_get_device_by_identifier.return_value = None

    def refresh(self, dr, *equipment: BrewCreatorEquipment, success: bool = True):
        dr.DeviceEntryDisabler = DeviceEntryDisabler
        dr.async_get.return_value = self.registry
        dr.async_entries_for_config_entry.return_value = list(self.devices)
        self.registry.async_update_device.reset_mock()
        self.coordinator.last_update_success = success
        if success:
            self.coordinator.data = BrewCreatorData(
                equipment={e.id: e for e in equipment}
            )
        self.coordinator._async_refresh_finished()

    def disabled_by_updates(self) -> dict[str, object]:
        return {
            c.args[0]: c.kwargs["disabled_by"]
            for c in self.registry.async_update_device.call_args_list
            if "disabled_by" in c.kwargs
        }

    def test_listener_gets_known_equipment_then_new_equipment(self, dr):
        added = []
        self.refresh(dr, ferminator(), tilt("t1"), unsupported())
        self.coordinator.async_add_equipment_listener(
            lambda equipment: added.append(sorted(e.id for e in equipment)) or set()
        )
        self.assertEqual([["f", "t1"]], added)

        self.refresh(dr, ferminator(), tilt("t1"), tilt("t2"), unsupported())
        self.assertEqual([["f", "t1"], ["t2"]], added)

        self.refresh(dr, ferminator(), tilt("t1"), tilt("t2"), unsupported())
        self.assertEqual([["f", "t1"], ["t2"]], added)

    def test_device_disabled_on_first_refresh_without_its_equipment(self, dr):
        self.devices = [device("f"), device("t1"), device("t2")]
        self.refresh(dr, ferminator(), tilt("t1"))
        self.assertEqual({"device-t2": INTEGRATION}, self.disabled_by_updates())

    def test_device_already_disabled_by_integration_is_left_alone(self, dr):
        self.devices = [device("f"), device("t1", disabled_by=INTEGRATION)]
        self.refresh(dr, ferminator())
        self.assertEqual({}, self.disabled_by_updates())

    def test_device_enabled_again_when_its_equipment_comes_back(self, dr):
        self.devices = [device("f"), device("t1", disabled_by=INTEGRATION)]
        self.refresh(dr, ferminator(), tilt("t1"))
        self.assertEqual({"device-t1": None}, self.disabled_by_updates())

    def test_device_disabled_by_user_is_never_touched(self, dr):
        self.devices = [
            device("f", disabled_by=DeviceEntryDisabler.USER),
            device("t1", disabled_by=DeviceEntryDisabler.USER),
        ]
        self.refresh(dr, ferminator())
        self.assertEqual({}, self.disabled_by_updates())

    def test_equipment_with_new_id_leaves_old_device_disabled(self, dr):
        added = []
        self.refresh(dr, ferminator(), tilt("t1"))
        self.coordinator.async_add_equipment_listener(
            lambda equipment: added.append([e.id for e in equipment]) or set()
        )
        self.devices = [device("f"), device("t1", disabled_by=INTEGRATION)]
        self.refresh(dr, ferminator(), tilt("t1-new"))
        self.assertEqual({}, self.disabled_by_updates())
        self.assertEqual(["t1-new"], added[-1])

    def test_no_disabling_after_failed_refresh(self, dr):
        self.refresh(dr, ferminator(), tilt("t1"))
        self.devices = [device("f"), device("t1")]
        self.refresh(dr, success=False)
        self.assertEqual({}, self.disabled_by_updates())

    def test_no_disabling_on_empty_list(self, dr):
        self.devices = [device("f"), device("t1")]
        self.refresh(dr)
        self.assertEqual({}, self.disabled_by_updates())

    def test_unsupported_equipment_counts_as_present(self, dr):
        self.devices = [device("f"), device("p")]
        self.refresh(dr, ferminator(), unsupported())
        self.assertEqual({}, self.disabled_by_updates())

    def test_device_info_is_synced(self, dr):
        self.refresh(dr, ferminator())
        self.registry.async_get_device_by_identifier.return_value = device("f")
        self.refresh(dr, ferminator(name="Renamed", sw_version="2.0"))
        self.registry.async_get_device_by_identifier.assert_called_with(("brewcreator", "f"), "entry")
        # The registry itself skips fields that didn't change
        self.registry.async_update_device.assert_called_once_with(
            "device-f",
            name="Renamed",
            sw_version="2.0",
            hw_version="A",
            model="Ferminator",
            serial_number="serial-f",
        )


class SyncIsolationTestCase(unittest.TestCase):
    @patch("custom_components.brewcreator.coordinator.ir", MagicMock())
    @patch("custom_components.brewcreator.coordinator.dr", MagicMock())
    def test_a_failing_step_does_not_stop_the_others(self):
        coordinator = make_coordinator()
        listener = MagicMock(return_value=set())
        coordinator._equipment_listeners = {listener: set()}
        coordinator.data = BrewCreatorData(equipment={"t1": tilt("t1")})
        coordinator._sync_device_enabled = MagicMock(side_effect=RuntimeError("boom"))
        coordinator._sync_device_enabled.__name__ = "_sync_device_enabled"
        coordinator._update_device_info = MagicMock()
        coordinator._update_device_info.__name__ = "_update_device_info"
        with self.assertLogs("custom_components.brewcreator.coordinator", "ERROR"):
            coordinator._async_refresh_finished()
        coordinator._update_device_info.assert_called_once()
        listener.assert_called_once()
        self.assertEqual({"t1"}, coordinator._equipment_listeners[listener])

    @patch("custom_components.brewcreator.coordinator.ir", MagicMock())
    @patch("custom_components.brewcreator.coordinator.dr", MagicMock())
    def test_equipment_with_unexpected_data_does_not_stop_the_others(self):
        coordinator = make_coordinator()
        bad = tilt("bad", color="TiltUnknown")
        coordinator.data = BrewCreatorData(equipment={"bad": bad, "t1": tilt("t1")})
        coordinator._async_refresh_finished()
        entry = MagicMock()
        entry.runtime_data = coordinator
        add_entities = MagicMock()

        with self.assertLogs("custom_components.brewcreator.entity", "ERROR"):
            register_hydrometer_entities(
                entry, add_entities, lambda c, id: [HydrometerSpecificGravityEntity(c, id)]
            )
        self.assertEqual(
            ["brewcreator_t1_sg"], [e.unique_id for e in add_entities.call_args.args[0]]
        )

    @patch("custom_components.brewcreator.coordinator.ir", MagicMock())
    @patch("custom_components.brewcreator.coordinator.dr", MagicMock())
    def test_equipment_whose_entities_failed_is_retried(self):
        coordinator = make_coordinator()
        coordinator.data = BrewCreatorData(
            equipment={"bad": tilt("bad", color="TiltUnknown"), "t1": tilt("t1")}
        )
        entry = MagicMock()
        entry.runtime_data = coordinator
        add_entities = MagicMock()
        with self.assertLogs("custom_components.brewcreator.entity", "ERROR"):
            register_hydrometer_entities(
                entry, add_entities, lambda c, id: [HydrometerSpecificGravityEntity(c, id)]
            )

        coordinator.data = BrewCreatorData(equipment={"bad": tilt("bad"), "t1": tilt("t1")})
        coordinator._async_refresh_finished()
        # Only the equipment that failed is added again
        self.assertEqual(2, add_entities.call_count)
        self.assertEqual(
            ["brewcreator_bad_sg"], [e.unique_id for e in add_entities.call_args.args[0]]
        )

        coordinator._async_refresh_finished()
        self.assertEqual(2, add_entities.call_count)


class UpdateDataTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_fetch_errors_become_update_failed(self):
        coordinator = make_coordinator()
        coordinator._api = MagicMock()
        coordinator._api.list_equipment = AsyncMock(side_effect=BrewCreatorError("503"))
        with self.assertRaises(UpdateFailed):
            await coordinator._async_update_data()


class WebsocketMessageTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_messages_and_reconnects_request_a_refresh(self):
        coordinator = make_coordinator()
        coordinator.async_request_refresh = AsyncMock()
        await coordinator._on_websocket_message(DEVICETWIN_MESSAGE)
        await coordinator._on_websocket_message(None)
        self.assertEqual(2, coordinator.async_request_refresh.await_count)


class EntityRegistrationTestCase(unittest.TestCase):
    @patch("custom_components.brewcreator.coordinator.ir", MagicMock())
    @patch("custom_components.brewcreator.coordinator.dr", MagicMock())
    def test_entities_added_for_equipment_appearing_later(self):
        coordinator = make_coordinator()
        coordinator.data = BrewCreatorData(equipment={"t1": tilt("t1")})
        coordinator._async_refresh_finished()
        entry = MagicMock()
        entry.runtime_data = coordinator
        add_entities = MagicMock()

        register_hydrometer_entities(
            entry,
            add_entities,
            lambda c, id: [HydrometerSpecificGravityEntity(c, id)],
        )
        entry.async_on_unload.assert_called_once()
        self.assertEqual(
            ["brewcreator_t1_sg"], [e.unique_id for e in add_entities.call_args.args[0]]
        )
        self.assertEqual({"update_before_add": False}, add_entities.call_args.kwargs)

        coordinator.data = BrewCreatorData(
            equipment={"t1": tilt("t1"), "t2": tilt("t2"), "f": ferminator()}
        )
        coordinator._async_refresh_finished()
        self.assertEqual(
            ["brewcreator_t2_sg"], [e.unique_id for e in add_entities.call_args.args[0]]
        )


LISTENER = MagicMock()


class RemoveDeviceTestCase(unittest.TestCase):
    def remove(self, *equipment) -> bool:
        coordinator = make_coordinator()
        coordinator.data = BrewCreatorData(equipment={e.id: e for e in equipment})
        coordinator._equipment_listeners = {LISTENER: {"t1"}}
        entry = SimpleNamespace(runtime_data=coordinator)
        result = asyncio.run(
            async_remove_config_entry_device(MagicMock(), entry, device("t1"))
        )
        return result, coordinator._equipment_listeners[LISTENER]

    def test_device_still_in_brewcreator_cannot_be_removed(self):
        result, added = self.remove(tilt("t1"))
        self.assertFalse(result)
        self.assertEqual({"t1"}, added)

    def test_device_gone_from_brewcreator_can_be_removed(self):
        result, added = self.remove(ferminator())
        self.assertTrue(result)
        self.assertEqual(set(), added)


if __name__ == "__main__":
    unittest.main()
