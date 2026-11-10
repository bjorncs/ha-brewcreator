import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.exceptions import ConfigEntryNotReady

from custom_components.brewcreator import async_setup_entry


@patch("custom_components.brewcreator.BrewCreatorTokenStore", MagicMock())
@patch("custom_components.brewcreator.async_get_clientsession", MagicMock())
@patch("custom_components.brewcreator.BrewCreatorAPI", MagicMock())
@patch("custom_components.brewcreator.BrewCreatorDataUpdateCoordinator")
class SetupEntryTestCase(unittest.TestCase):
    def setup_entry(self, coordinator_class, first_refresh: AsyncMock):
        coordinator = coordinator_class.return_value
        coordinator.async_config_entry_first_refresh = first_refresh
        coordinator.close = AsyncMock()
        hass = MagicMock()
        hass.config_entries.async_forward_entry_setups = AsyncMock()
        entry = SimpleNamespace(data={"username": "user", "password": "secret"})
        return coordinator, hass, entry

    def test_failed_first_refresh_stops_the_websocket(self, coordinator_class):
        coordinator, hass, entry = self.setup_entry(
            coordinator_class, AsyncMock(side_effect=ConfigEntryNotReady("503"))
        )
        with self.assertRaises(ConfigEntryNotReady):
            asyncio.run(async_setup_entry(hass, entry))
        coordinator.close.assert_awaited_once()
        hass.config_entries.async_forward_entry_setups.assert_not_awaited()

    def test_successful_setup_keeps_the_websocket(self, coordinator_class):
        coordinator, hass, entry = self.setup_entry(coordinator_class, AsyncMock())
        self.assertTrue(asyncio.run(async_setup_entry(hass, entry)))
        coordinator.close.assert_not_awaited()
        self.assertIs(coordinator, entry.runtime_data)


if __name__ == "__main__":
    unittest.main()
