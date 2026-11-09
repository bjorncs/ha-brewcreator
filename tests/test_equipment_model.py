import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from custom_components.brewcreator.api import (
    BrewCreatorAPI,
    BrewCreatorError,
    Ferminator,
    parse_signalr_frames,
)
from tests.factories import tilt


class UpdateEquipmentStateTestCase(unittest.IsolatedAsyncioTestCase):
    def api(self, response) -> BrewCreatorAPI:
        api = BrewCreatorAPI("user", "pass", MagicMock(), MagicMock())
        api._BrewCreatorAPI__do_authenticated_request = AsyncMock(return_value=response)
        return api

    async def test_accepted_update(self):
        await self.api({"succeeded": True, "data": {}})._update_equipment_state("f", {"fanSpeed": 2})

    async def test_rejected_update_raises(self):
        for response in ({"succeeded": False}, {}, None):
            with self.subTest(response=response), self.assertRaises(BrewCreatorError):
                await self.api(response)._update_equipment_state("f", {"fanSpeed": 2})


class NullDeviceTwinTestCase(unittest.TestCase):
    def test_ferminator_without_device_twin(self):
        ferminator = Ferminator(MagicMock(), {"id": "f", "deviceTwinState": None})
        self.assertFalse(ferminator.is_connected)
        self.assertIsNone(ferminator.sw_version)
        self.assertIsNone(ferminator.hw_version)


class NullLastActivityTimeTestCase(unittest.TestCase):
    def test_hydrometer_without_brew_log_has_no_last_activity_time(self):
        self.assertIsNone(tilt().last_activity_time)
        self.assertIsNone(tilt(lastActivityTime=None).last_activity_time)
        del_key = tilt()
        del del_key.json["lastActivityTime"]
        self.assertIsNone(del_key.last_activity_time)

    def test_hydrometer_last_activity_time_is_converted_from_danish_time(self):
        value = tilt(lastActivityTime="2026-11-08T18:34:09.701+00:00").last_activity_time
        self.assertEqual(datetime(2026, 11, 8, 17, 34, 9, 701000, timezone.utc), value)

    def test_ferminator_without_last_activity_time(self):
        ferminator = Ferminator(MagicMock(), {"id": "f", "lastActivityTime": None})
        self.assertIsNone(ferminator.last_activity_time)

    def test_ferminator_without_process_has_no_mode(self):
        ferminator = Ferminator(MagicMock(), {"id": "f", "lProcess": None})
        self.assertIsNone(ferminator.mode)


class SpecificGravityRangeTestCase(unittest.TestCase):
    def test_out_of_range_values_are_rejected(self):
        for sg in (0, 0.0, 0.79, 1.31, 5):
            with self.subTest(sg=sg):
                self.assertIsNone(tilt(sg=sg).specific_gravity)

    def test_bounds_and_plausible_values_are_kept(self):
        for sg in (0.8, 1.0, 1.018, 1.2, 1.25, 1.3):
            with self.subTest(sg=sg):
                self.assertEqual(sg, tilt(sg=sg).specific_gravity)

    def test_missing_value(self):
        self.assertIsNone(tilt(sg=None).specific_gravity)


class SignalRParsingTestCase(unittest.TestCase):
    def test_splits_frames_on_record_separator(self):
        data = '{"type":6}\x1e{"type":1,"target":"devicetwin","arguments":[]}\x1e'
        self.assertEqual(
            [{"type": 6}, {"type": 1, "target": "devicetwin", "arguments": []}],
            parse_signalr_frames(data),
        )

    def test_frame_without_record_separator(self):
        self.assertEqual([{"type": 6}], parse_signalr_frames('{"type":6}'))

    def test_skips_unparsable_records(self):
        self.assertEqual(
            [{"type": 7}], parse_signalr_frames('not json\x1e[1]\x1e{"type":7}\x1e')
        )


if __name__ == "__main__":
    unittest.main()
