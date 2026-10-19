import asyncio
from datetime import datetime
import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

_LOGGER = logging.getLogger(__name__)


class BrewCreatorTokenStore:
    def __init__(self, hass: HomeAssistant):
        self._store = Store(hass, 1, "brewcreator_tokens")
        self._lock = asyncio.Lock()

    async def load_tokens(self) -> tuple[str | None, str | None, datetime | None]:
        async with self._lock:
            data = await self._store.async_load()
            if data is None:
                return None, None, None
            try:
                expire_time = data.get("expire_time")
                return (
                    data.get("access_token"),
                    data.get("refresh_token"),
                    datetime.fromisoformat(expire_time)
                    if expire_time is not None
                    else None,
                )
            except (AttributeError, TypeError, ValueError):
                _LOGGER.warning("Ignoring invalid token storage")
                return None, None, None

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
