import asyncio
import logging
import os
import unittest

from custom_components.brewcreator.api import BrewCreatorAPI, BrewCreatorEquipment, Ferminator


class MyTestCase(unittest.IsolatedAsyncioTestCase):

    @classmethod
    def setUpClass(cls):
        logging.basicConfig(level=logging.DEBUG)

    async def asyncSetUp(self):
        username = os.getenv("BREWCREATOR_USERNAME")
        password = os.getenv("BREWCREATOR_PASSWORD")
        if not username or not password:
            raise ValueError("BREWCREATOR_USERNAME and BREWCREATOR_PASSWORD must be set")
        self.api = BrewCreatorAPI(username, password)

    async def asyncTearDown(self):
        await self.api.close()

    async def test_list_equipment(self):
        equipments = await self.api.list_equipment()
        self.assertIsInstance(equipments, dict)
        self.assertGreater(len(equipments), 0)

    async def test_control_ferminator(self):
        equipments = await self.api.list_equipment()
        ferminator: Ferminator = next(e for e in equipments.values() if isinstance(e, Ferminator))
        self.assertTrue(await ferminator.set_fan_speed(4))
        self.assertTrue(await ferminator.set_target_temperature(1))

    async def test_websocket(self):
        await self.api.start_websocket(print_equipment)
        await asyncio.sleep(10)
        await self.api.stop_websocket()


async def print_equipment(equipment: dict[str, BrewCreatorEquipment]):
    for e in equipment.items():
        print(f"{e[0]}: {e[1].json}")

if __name__ == '__main__':
    unittest.main()
