import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .api import BrewCreatorAPI, BrewCreatorEquipment, UnsupportedEquipment
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

UNSUPPORTED_ISSUE_PREFIX = "unsupported_equipment_"


class BrewCreatorDataUpdateCoordinator(
    DataUpdateCoordinator[dict[str, BrewCreatorEquipment]]
):
    """Coordinator for updating data from BrewCreator API."""

    def __init__(
        self,
        hass: HomeAssistant,
        api: BrewCreatorAPI,
        entry: ConfigEntry["BrewCreatorDataUpdateCoordinator"],
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            always_update=True,
            update_interval=None,
            update_method=None,
            config_entry=entry,
        )
        self._api: BrewCreatorAPI = api
        # Include issues raised before a restart so stale ones are also removed
        self._unsupported_issue_ids: set[str] = {
            issue_id
            for domain, issue_id in ir.async_get(hass).issues
            if domain == DOMAIN and issue_id.startswith(UNSUPPORTED_ISSUE_PREFIX)
        }

    async def _async_setup(self):
        await self._api.start_websocket(self._on_equipment_update)

    async def _async_update_data(self) -> dict[str, BrewCreatorEquipment]:
        equipment_list = await self._api.list_equipment()
        self._sync_unsupported_issues(equipment_list)
        return equipment_list

    async def _on_equipment_update(
        self, equipment_list: dict[str, BrewCreatorEquipment]
    ) -> None:
        _LOGGER.debug("Received equipment update: %s", equipment_list)
        self._sync_unsupported_issues(equipment_list)
        self.async_set_updated_data(equipment_list)

    def _sync_unsupported_issues(
        self, equipment_list: dict[str, BrewCreatorEquipment]
    ) -> None:
        """Raise a repair issue per unsupported equipment type, and remove stale ones."""
        names_by_type: dict[str, list[str]] = {}
        for equipment in equipment_list.values():
            if isinstance(equipment, UnsupportedEquipment):
                names_by_type.setdefault(equipment.raw_type, []).append(equipment.name)
        issue_ids = set()
        for equipment_type, names in names_by_type.items():
            issue_id = f"{UNSUPPORTED_ISSUE_PREFIX}{equipment_type}"
            issue_ids.add(issue_id)
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="unsupported_equipment",
                translation_placeholders={
                    "type": equipment_type,
                    "names": ", ".join(sorted(names)),
                },
                learn_more_url="https://github.com/bjorncs/ha-brewcreator/issues",
            )
        for issue_id in self._unsupported_issue_ids - issue_ids:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)
        self._unsupported_issue_ids = issue_ids

    @property
    def api(self) -> BrewCreatorAPI:
        return self._api

    async def close(self) -> None:
        await self._api.close()
