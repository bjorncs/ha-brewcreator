import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock

from homeassistant.const import EntityCategory
from homeassistant.exceptions import HomeAssistantError

from custom_components.brewcreator.api import BrewCreatorError, linkable_hydrometers
from custom_components.brewcreator.coordinator import BrewCreatorData
from custom_components.brewcreator.select import FerminatorHydrometerEntity
from tests.factories import ferminator, graviator, tilt, unsupported


def setup(*equipment):
    """Return the entity of Ferminator "f" among the equipment, with links resolved like list_equipment does."""
    for e in equipment:
        if hasattr(e, "_update_connected_equipment"):
            e._update_connected_equipment(list(equipment))
    by_id = {e.id: e for e in equipment}
    by_id["f"]._api._update_equipment_state = AsyncMock(return_value=None)
    coordinator = MagicMock()
    coordinator.async_refresh = AsyncMock()
    coordinator.data = BrewCreatorData(equipment=by_id)
    return FerminatorHydrometerEntity(coordinator, "f")


class LinkableHydrometersTestCase(unittest.TestCase):
    def test_hydrometers_not_linked_elsewhere_ordered_by_name(self):
        f = ferminator("f", connected=["t1"])
        other = ferminator("o", connected=["t2"])
        equipment = {
            e.id: e
            for e in (
                f,
                other,
                tilt("t1", name="Tilt Orange"),
                tilt("t2", name="Tilt Red"),
                tilt("t3", name="Tilt Black"),
                graviator("g", name="Graviator"),
                unsupported("p"),
            )
        }
        self.assertEqual(
            ["g", "t3", "t1"], [h.id for h in linkable_hydrometers(f, equipment)]
        )


class HydrometerSelectTestCase(unittest.TestCase):
    def test_options_and_current_option(self):
        entity = setup(
            ferminator("f", connected=["t1"]),
            ferminator("o", connected=["t2"]),
            tilt("t1", name="Tilt Black"),
            tilt("t2", name="Tilt Red"),
            tilt("t3", name="Tilt Orange"),
        )
        self.assertEqual(["none", "Tilt Black", "Tilt Orange"], entity.options)
        self.assertEqual("Tilt Black", entity.current_option)
        self.assertEqual(EntityCategory.CONFIG, entity.entity_category)

    def test_nothing_linked(self):
        entity = setup(ferminator("f"), tilt("t1", name="Tilt Black"))
        self.assertEqual("none", entity.current_option)

    def test_options_when_the_ferminator_is_gone(self):
        entity = setup(ferminator("f"), tilt("t1", name="Tilt Black"))
        entity.coordinator.data = BrewCreatorData(equipment={})
        self.assertFalse(entity.available)
        self.assertEqual({"options": ["none"]}, entity.capability_attributes)

    def test_duplicate_names_get_an_id_suffix(self):
        entity = setup(
            ferminator("f", connected=["aaaaaaaa-1"]),
            tilt("aaaaaaaa-1", name="Tilt"),
            tilt("bbbbbbbb-2", name="Tilt"),
        )
        self.assertEqual(["none", "Tilt (aaaaaaaa)", "Tilt (bbbbbbbb)"], entity.options)
        self.assertEqual("Tilt (aaaaaaaa)", entity.current_option)

    def test_selecting_a_hydrometer_links_it(self):
        entity = setup(
            ferminator("f", connected=["t1"]),
            tilt("t1", name="Tilt Black"),
            tilt("t2", name="Tilt Orange"),
        )
        asyncio.run(entity.async_select_option("Tilt Orange"))
        f = entity.coordinator.data.equipment["f"]
        f._api._update_equipment_state.assert_awaited_once_with(
            "f", {"connectedEquipments": ["t2"]}
        )
        entity.coordinator.async_refresh.assert_awaited_once()

    def test_selecting_none_unlinks(self):
        entity = setup(ferminator("f", connected=["t1"]), tilt("t1", name="Tilt Black"))
        asyncio.run(entity.async_select_option("none"))
        f = entity.coordinator.data.equipment["f"]
        f._api._update_equipment_state.assert_awaited_once_with(
            "f", {"connectedEquipments": []}
        )

    def test_failed_link_raises(self):
        entity = setup(ferminator("f"), tilt("t1", name="Tilt Black"))
        f = entity.coordinator.data.equipment["f"]
        f._api._update_equipment_state.side_effect = BrewCreatorError("rejected")
        with self.assertRaises(HomeAssistantError) as raised:
            asyncio.run(entity.async_select_option("Tilt Black"))
        self.assertEqual("link_failed", raised.exception.translation_key)
        entity.coordinator.async_refresh.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
