import unittest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.brewcreator.token_store import BrewCreatorTokenStore


class TokenStoreTestCase(unittest.IsolatedAsyncioTestCase):
    async def load(self, data):
        with patch("custom_components.brewcreator.token_store.Store") as store_cls:
            store_cls.return_value.async_load = AsyncMock(return_value=data)
            return await BrewCreatorTokenStore(MagicMock()).load_tokens()

    async def test_missing_storage(self):
        self.assertEqual((None, None, None), await self.load(None))

    async def test_valid_tokens(self):
        expire_time = datetime(2026, 10, 19, 12, 0, 0)
        data = {
            "access_token": "a",
            "refresh_token": "r",
            "expire_time": expire_time.isoformat(),
        }
        self.assertEqual(("a", "r", expire_time), await self.load(data))

    async def test_null_expire_time(self):
        data = {"access_token": None, "refresh_token": None, "expire_time": None}
        self.assertEqual((None, None, None), await self.load(data))

    async def test_invalid_expire_time(self):
        data = {"access_token": "a", "refresh_token": "r", "expire_time": "garbage"}
        self.assertEqual((None, None, None), await self.load(data))

    async def test_missing_keys(self):
        self.assertEqual((None, None, None), await self.load({}))


if __name__ == "__main__":
    unittest.main()
