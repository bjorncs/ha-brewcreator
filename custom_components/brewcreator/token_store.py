import asyncio
from datetime import datetime

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store


class BrewCreatorTokenStore:
    def __init__(self, hass: HomeAssistant):
        self._store = Store(hass, 1, "brewcreator_tokens")
        self._lock = asyncio.Lock()

    async def load_tokens(self) -> tuple[str | None, str | None, datetime | None]:
        async with self._lock:
            data = await self._store.async_load()
            if data is None:
                return None, None, None
            return (
                data["access_token"],
                data["refresh_token"],
                datetime.fromisoformat(data["expire_time"]),
            )

    async def save_tokens(
        self,
        access_token: str | None,
        refresh_token: str | None,
        expire_time: datetime | None,
    ):
        async with self._lock:
            data = {
                "access_token": access_token,
                "refresh_token": refresh_token,
                "expire_time": expire_time.isoformat()
                if expire_time is not None
                else None,
            }
            await self._store.async_save(data)
