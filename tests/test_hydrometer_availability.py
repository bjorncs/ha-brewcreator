import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

from custom_components.brewcreator.api import Ferminator, Tilt
from custom_components.brewcreator.sensor import HydrometerSpecificGravityEntity


def ferminator(connection_state: str) -> Ferminator:
    return Ferminator(
        MagicMock(),
        {
            "id": "f",
            "name": "Ferminator",
            "iotHubBrewEquipmentGroupId": "Ferminator",
            "iotHubBrewEquipmentId": "serial-f",
            "connectedEquipments": ["t"],
            "deviceTwinState": {"connectionState": connection_state},
        },
    )


def tilt(last_activity_ago: timedelta, linked_to: str | None = None) -> Tilt:
    # The last activity time is reported in Danish local time, see Hydrometer.last_activity_time
    copenhagen_now = datetime.now(ZoneInfo("Europe/Copenhagen")).replace(tzinfo=None)
    last_activity = copenhagen_now - last_activity_ago
    return Tilt(
        MagicMock(),
        {
            "id": "t",
            "name": "Tilt",
            "iotHubBrewEquipmentGroupId": "Tilt",
            "iotHubBrewEquipmentId": "serial-t",
            "color": "TiltOrange",
            "sg": 1.014,
            "actualTemperature": 20,
            "lastActivityTime": last_activity.isoformat(),
            "equipmentId": linked_to,
        },
    )


class HydrometerAvailabilityTestCase(unittest.TestCase):
    def is_available(self, *equipment) -> bool:
        coordinator = MagicMock()
        coordinator.data = {e.id: e for e in equipment}
        return HydrometerSpecificGravityEntity(coordinator, "t").available

    def test_available_with_recent_activity(self):
        self.assertTrue(self.is_available(tilt(timedelta(hours=1))))

    def test_unavailable_with_stale_activity(self):
        self.assertFalse(self.is_available(tilt(timedelta(days=5))))

    def test_available_with_stale_activity_when_linked_ferminator_is_connected(self):
        self.assertTrue(
            self.is_available(
                tilt(timedelta(days=5), linked_to="f"), ferminator("Connected")
            )
        )

    def test_unavailable_with_stale_activity_when_linked_ferminator_is_disconnected(self):
        self.assertFalse(
            self.is_available(
                tilt(timedelta(days=5), linked_to="f"), ferminator("Disconnected")
            )
        )


if __name__ == "__main__":
    unittest.main()
