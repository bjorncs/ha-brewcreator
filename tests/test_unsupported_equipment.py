import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.brewcreator.api import (
    BrewCreatorAPI,
    Ferminator,
    Tilt,
    UnsupportedEquipment,
)
from custom_components.brewcreator.coordinator import (
    BrewCreatorDataUpdateCoordinator,
)


def equipment(id: str, type: str, name: str, connected: list[str] = ()) -> dict:
    return {
        "id": id,
        "name": name,
        "iotHubBrewEquipmentGroupId": type,
        "connectedEquipments": list(connected),
    }


class ListEquipmentTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_unsupported_equipment_does_not_break_listing(self):
        api = BrewCreatorAPI("user", "pass", MagicMock(), MagicMock())
        data = {
            "data": [
                equipment("f", "Ferminator", "Ferminator", connected=["t", "g"]),
                equipment("t", "Tilt", "Tilt"),
                equipment("g", "Graviator", "My Graviator"),
            ]
        }
        with patch.object(api, "equipment_json", AsyncMock(return_value=data)):
            result = await api.list_equipment()

        self.assertIsInstance(result["f"], Ferminator)
        self.assertIsInstance(result["t"], Tilt)
        self.assertIsInstance(result["g"], UnsupportedEquipment)
        self.assertEqual("Graviator", result["g"].raw_type)
        self.assertEqual(
            {"t", "g"}, {e.id for e in result["f"].connected_equipment}
        )


class UnsupportedIssuesTestCase(unittest.TestCase):
    def sync(self, coordinator, equipment_list):
        BrewCreatorDataUpdateCoordinator._sync_unsupported_issues(
            coordinator, equipment_list
        )

    @patch("custom_components.brewcreator.coordinator.ir")
    def test_creates_and_deletes_issues(self, ir):
        api = MagicMock()
        coordinator = SimpleNamespace(hass=MagicMock(), _unsupported_issue_ids=set())
        graviator = UnsupportedEquipment(api, equipment("g", "Graviator", "G1"))
        tilt = Tilt(api, equipment("t", "Tilt", "T1"))

        self.sync(coordinator, {"g": graviator, "t": tilt})
        ir.async_create_issue.assert_called_once()
        args, kwargs = ir.async_create_issue.call_args
        self.assertEqual("unsupported_equipment_Graviator", args[2])
        self.assertEqual(
            {"type": "Graviator", "names": "G1"}, kwargs["translation_placeholders"]
        )
        ir.async_delete_issue.assert_not_called()

        self.sync(coordinator, {"t": tilt})
        ir.async_delete_issue.assert_called_once_with(
            coordinator.hass, "brewcreator", "unsupported_equipment_Graviator"
        )
        self.assertEqual(set(), coordinator._unsupported_issue_ids)


if __name__ == "__main__":
    unittest.main()
