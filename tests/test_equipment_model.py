import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import aiohttp
from multidict import CIMultiDict, CIMultiDictProxy
from yarl import URL

from custom_components.brewcreator.api import (
    BrewCreatorAPI,
    BrewCreatorError,
    Ferminator,
    parse_signalr_frames,
)
from tests.factories import ferminator, tilt


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


class TransportErrorTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_transport_errors_are_raised_as_brewcreator_errors(self):
        for error in (aiohttp.ClientConnectionError("refused"), TimeoutError()):
            with self.subTest(error=error):
                session = MagicMock()
                session.request.side_effect = error
                api = BrewCreatorAPI("user", "pass", MagicMock(), session)
                api._BrewCreatorAPI__initial_token_load_completed = True
                api._BrewCreatorAPI__access_token = "token"
                api._BrewCreatorAPI__expire_time = datetime.now() + timedelta(hours=1)
                with self.assertRaises(BrewCreatorError) as raised:
                    await api.equipment_json()
                self.assertIs(error, raised.exception.__cause__)

    async def test_access_token_is_left_out_of_the_error(self):
        request_info = aiohttp.RequestInfo(
            URL("https://api.brewcreator.com/x"), "GET", CIMultiDictProxy(CIMultiDict(Authorization="Bearer token"))
        )
        session = MagicMock()
        session.request.side_effect = aiohttp.ContentTypeError(request_info, (), message="not JSON")
        api = BrewCreatorAPI("user", "pass", MagicMock(), session)
        api._BrewCreatorAPI__initial_token_load_completed = True
        api._BrewCreatorAPI__access_token = "token"
        api._BrewCreatorAPI__expire_time = datetime.now() + timedelta(hours=1)
        with self.assertRaises(BrewCreatorError) as raised:
            await api.equipment_json()
        self.assertIn("ContentTypeError", str(raised.exception))
        self.assertNotIn("Bearer", str(raised.exception))


class ConnectivityTestCase(unittest.TestCase):
    def test_online_while_active_or_briefly_inactive(self):
        cases = [
            (True, "Connected", False, True),
            # isActive drops for a short while when the Ferminator reconnects to the cloud
            (False, "Connected", False, True),
            # connectionState can stay Connected for minutes after the Ferminator has gone
            (False, "Connected", True, False),
            # Just reconnected: connectionState lags for minutes
            (True, "Disconnected", False, True),
            (True, "Disconnected", True, True),
            (False, "Disconnected", False, False),
        ]
        for is_active, connection_state, inactive_too_long, expected in cases:
            with self.subTest(
                is_active=is_active, connection_state=connection_state, inactive_too_long=inactive_too_long
            ):
                f = ferminator(connection_state=connection_state, is_active=is_active)
                f.inactive_too_long = inactive_too_long
                self.assertEqual(expected, f.is_connected)

    def test_active_ferminator_without_device_twin_is_online(self):
        self.assertTrue(Ferminator(MagicMock(), {"id": "f", "isActive": True, "deviceTwinState": None}).is_connected)


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
        self.assertEqual(datetime(2026, 11, 8, 17, 34, 9, 701000, UTC), value)

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


class AbvTestCase(unittest.TestCase):
    def test_abv_with_an_original_gravity(self):
        self.assertEqual(5.2224464, tilt(og=1.058, abv=5.2224464).abv)

    def test_abv_without_an_original_gravity_is_unknown(self):
        # BrewCreator reports ABV 0 while the hydrometer's original gravity isn't set
        for og in (0, None):
            with self.subTest(og=og):
                self.assertIsNone(tilt(og=og, abv=0).abv)


class TemperatureRangeTestCase(unittest.TestCase):
    def test_out_of_range_values_are_rejected(self):
        for temperature in (-20.1, -100, 120.1, 1000):
            with self.subTest(temperature=temperature):
                self.assertIsNone(tilt(temperature=temperature).actual_temperature)
                self.assertIsNone(ferminator(actualTemperature=temperature).actual_temperature_builtin_probe)

    def test_bounds_and_plausible_values_are_kept(self):
        for temperature in (-20, 0, 20.2, 120):
            with self.subTest(temperature=temperature):
                self.assertEqual(temperature, tilt(temperature=temperature).actual_temperature)
                self.assertEqual(
                    temperature,
                    ferminator(actualTemperature=temperature).actual_temperature_builtin_probe,
                )

    def test_missing_value(self):
        self.assertIsNone(tilt(temperature=None).actual_temperature)
        self.assertIsNone(ferminator(actualTemperature=None).actual_temperature_builtin_probe)


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
        self.assertEqual([{"type": 7}], parse_signalr_frames('not json\x1e[1]\x1e{"type":7}\x1e'))


if __name__ == "__main__":
    unittest.main()
