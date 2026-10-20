import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.brewcreator.api import (
    BrewCreatorAPI,
    Ferminator,
    Graviator,
    Hydrometer,
    Tilt,
    UnsupportedEquipment,
)
from custom_components.brewcreator.coordinator import (
    BrewCreatorDataUpdateCoordinator,
)
from custom_components.brewcreator.entity import hydrometer_device_info


def equipment(
    id: str, type: str, name: str, connected: list[str] = (), **extra
) -> dict:
    return {
        "id": id,
        "name": name,
        "iotHubBrewEquipmentGroupId": type,
        "iotHubBrewEquipmentId": f"serial-{id}",
        "connectedEquipments": list(connected),
        **extra,
    }


class ListEquipmentTestCase(unittest.IsolatedAsyncioTestCase):
    async def list_equipment(self, *equipment_json):
        api = BrewCreatorAPI("user", "pass", MagicMock(), MagicMock())
        data = {"data": list(equipment_json)}
        with patch.object(api, "equipment_json", AsyncMock(return_value=data)):
            return await api.list_equipment()

    async def test_unsupported_equipment_does_not_break_listing(self):
        result = await self.list_equipment(
            equipment("f", "Ferminator", "Ferminator", connected=["t", "p"]),
            equipment("t", "Tilt", "Tilt"),
            equipment("p", "Plaato", "My Plaato"),
        )

        self.assertIsInstance(result["f"], Ferminator)
        self.assertIsInstance(result["t"], Tilt)
        self.assertIsInstance(result["p"], UnsupportedEquipment)
        self.assertEqual("Plaato", result["p"].raw_type)
        self.assertEqual(
            {"t", "p"}, {e.id for e in result["f"].connected_equipment}
        )

    async def test_graviator_is_hydrometer(self):
        result = await self.list_equipment(
            equipment(
                "f", "Ferminator", "Ferminator", connected=["g"], actualTemperature=20.0
            ),
            equipment("g", "Graviator", "Graviator", sg=1.012, actualTemperature=18.5),
        )

        self.assertIsInstance(result["g"], Graviator)
        self.assertIsInstance(result["g"], Hydrometer)
        self.assertNotIsInstance(result["g"], Tilt)
        self.assertEqual(1.012, result["g"].specific_gravity)
        self.assertEqual(18.5, result["f"].actual_temperature)


class HydrometerDeviceInfoTestCase(unittest.TestCase):
    def test_graviator_device_info(self):
        graviator = Graviator(MagicMock(), equipment("g", "Graviator", "G1"))
        info = hydrometer_device_info(graviator)
        self.assertEqual("Brewolution", info["manufacturer"])
        self.assertEqual("Graviator", info["model"])
        self.assertEqual("serial-g", info["serial_number"])

    def test_tilt_device_info(self):
        tilt = Tilt(MagicMock(), equipment("t", "Tilt", "T1", color="TiltRed"))
        info = hydrometer_device_info(tilt)
        self.assertEqual("Tilt", info["manufacturer"])
        self.assertEqual("RED", info["model"])


class UnsupportedIssuesTestCase(unittest.TestCase):
    def sync(self, coordinator, equipment_list):
        BrewCreatorDataUpdateCoordinator._sync_unsupported_issues(
            coordinator, equipment_list
        )

    @patch("custom_components.brewcreator.coordinator.ir")
    def test_creates_and_deletes_issues(self, ir):
        api = MagicMock()
        coordinator = SimpleNamespace(hass=MagicMock(), _unsupported_issue_ids=set())
        plaato = UnsupportedEquipment(api, equipment("p", "Plaato", "P1"))
        tilt = Tilt(api, equipment("t", "Tilt", "T1"))

        self.sync(coordinator, {"p": plaato, "t": tilt})
        ir.async_create_issue.assert_called_once()
        args, kwargs = ir.async_create_issue.call_args
        self.assertEqual("unsupported_equipment_Plaato", args[2])
        self.assertEqual(
            {"type": "Plaato", "names": "P1"}, kwargs["translation_placeholders"]
        )
        ir.async_delete_issue.assert_not_called()

        self.sync(coordinator, {"t": tilt})
        ir.async_delete_issue.assert_called_once_with(
            coordinator.hass, "brewcreator", "unsupported_equipment_Plaato"
        )
        self.assertEqual(set(), coordinator._unsupported_issue_ids)


if __name__ == "__main__":
    unittest.main()
