import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HassJob, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    SUPPORTED_EQUIPMENT,
    BrewCreatorAPI,
    BrewCreatorEquipment,
    BrewCreatorError,
    Ferminator,
    UnsupportedEquipment,
)
from .const import DOMAIN
from .device import device_info, device_name, equipment_ids, find_device

_LOGGER = logging.getLogger(__name__)

UNSUPPORTED_ISSUE_PREFIX = "unsupported_equipment_"

# Websocket messages come in bursts, e.g. selectgrav followed by the link moving within a second.
# Wait this long after the first message, so the burst is fetched once, after it has settled.
REFRESH_COOLDOWN_SECONDS = 2

# The websocket doesn't cover everything: it only carries the telemetry of Ferminators, so changes BrewCreator
# doesn't push, such as readings from an unlinked Wi-Fi Graviator, are only fetched by this poll. It also fetches
# while the websocket is down, and retries a failed refresh.
FALLBACK_POLL_INTERVAL = timedelta(minutes=2)

# A Ferminator's isActive drops while it reconnects to the cloud, about every 48 minutes, for 8 to 34 seconds in two
# days of logs. It only counts as offline once it has been inactive for longer than this.
OFFLINE_GRACE = timedelta(seconds=60)

# BrewCreator sends Disconnected as soon as it notices a Ferminator is gone. Refresh again once the grace has passed,
# so a Ferminator that is switched off is seen as offline without waiting for the fallback poll.
DISCONNECT_REFRESH_DELAY = OFFLINE_GRACE + timedelta(seconds=10)

# Device registry fields kept in sync with BrewCreator, as they can change after the device is registered
SYNCED_DEVICE_FIELDS = ("name", "sw_version", "hw_version", "model", "serial_number")


@dataclass
class BrewCreatorData:
    """The coordinator's data."""

    equipment: dict[str, BrewCreatorEquipment]
    """All equipment on the account by equipment ID, including unsupported equipment."""


EquipmentListener = Callable[[list[BrewCreatorEquipment]], set[str]]
"""Called with equipment that should have entities added. Returns the IDs whose entities could not be created."""


class BrewCreatorDataUpdateCoordinator(DataUpdateCoordinator[BrewCreatorData]):
    """Coordinator for updating data from BrewCreator API."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        api: BrewCreatorAPI,
        entry: ConfigEntry[BrewCreatorDataUpdateCoordinator],
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            always_update=True,
            update_interval=FALLBACK_POLL_INTERVAL,
            config_entry=entry,
            request_refresh_debouncer=Debouncer(
                hass,
                _LOGGER,
                cooldown=REFRESH_COOLDOWN_SECONDS,
                immediate=False,
            ),
        )
        self._api: BrewCreatorAPI = api
        # Per listener, the IDs of the equipment it has added entities for. Kept while a device is disabled, as HA
        # reloads the entry when the device is enabled again.
        self._equipment_listeners: dict[EquipmentListener, set[str]] = {}
        # Include issues raised before a restart so stale ones are also removed
        self._unsupported_issue_ids: set[str] = {
            issue_id
            for domain, issue_id in ir.async_get(hass).issues
            if domain == DOMAIN and issue_id.startswith(UNSUPPORTED_ISSUE_PREFIX)
        }
        self._disconnect_refresh_unsub: CALLBACK_TYPE | None = None
        # When each inactive Ferminator was first seen inactive, by equipment ID
        self._inactive_since: dict[str, datetime] = {}

    async def _async_setup(self):
        await self._api.start_websocket(self._on_websocket_message)

    async def _async_update_data(self) -> BrewCreatorData:
        try:
            equipment = await self._api.list_equipment()
        except BrewCreatorError as err:
            raise UpdateFailed(f"Failed to list equipment: {err}") from err
        self._track_inactive_ferminators(equipment)
        return BrewCreatorData(equipment=equipment)

    def _track_inactive_ferminators(self, equipment: dict[str, BrewCreatorEquipment]) -> None:
        """Mark Ferminators that have been inactive for longer than OFFLINE_GRACE, see Ferminator.is_connected."""
        now = dt_util.utcnow()
        ferminators = [e for e in equipment.values() if isinstance(e, Ferminator)]
        self._inactive_since = {f.id: self._inactive_since.get(f.id, now) for f in ferminators if not f.is_active}
        for f in ferminators:
            since = self._inactive_since.get(f.id)
            f.inactive_too_long = since is not None and now - since >= OFFLINE_GRACE

    async def _on_websocket_message(self, message: dict[str, Any] | None) -> None:
        """Handle a websocket (re)connect (message is None) or a telemetry message."""
        if message is None:
            _LOGGER.debug("WebSocket connected, refreshing equipment")
        # Debounced, so this returns at once and a burst of messages is fetched once
        await self.async_request_refresh()
        if message is not None and _op_type(message) == "Disconnected":
            self._schedule_disconnect_refresh()

    @callback
    def _schedule_disconnect_refresh(self) -> None:
        """Refresh once the offline grace has passed, replacing a refresh already scheduled."""
        self._cancel_disconnect_refresh()
        self._disconnect_refresh_unsub = async_call_later(
            self.hass,
            DISCONNECT_REFRESH_DELAY,
            HassJob(self._async_disconnect_refresh, cancel_on_shutdown=True),
        )

    async def _async_disconnect_refresh(self, _now: datetime) -> None:
        self._disconnect_refresh_unsub = None
        await self.async_request_refresh()

    @callback
    def _cancel_disconnect_refresh(self) -> None:
        if self._disconnect_refresh_unsub is not None:
            self._disconnect_refresh_unsub()
            self._disconnect_refresh_unsub = None

    async def async_shutdown(self) -> None:
        """Stop the websocket and refreshes. HA calls this when the entry is unloaded or its setup fails."""
        self._cancel_disconnect_refresh()
        await self.close()
        await super().async_shutdown()

    async def async_send(self, update: Awaitable[None], translation_key: str, name: str) -> None:
        """Send an update to BrewCreator, then refresh so the new state shows when the action returns.

        A failed update raises HomeAssistantError, with the translation key and the equipment's name.
        """
        try:
            await update
        except BrewCreatorError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key=translation_key,
                translation_placeholders={"name": name, "error": str(err)},
            ) from err
        await self.async_refresh()

    @callback
    def _async_refresh_finished(self) -> None:
        """Sync devices, entities and issues with the equipment list after every refresh.

        Both the fallback poll and websocket-triggered refreshes end here, before listeners are updated.
        HA calls this outside its error handling, so a failing step is logged rather than raised: otherwise
        listeners would not be updated, and every entity would go stale.
        """
        if not self.last_update_success or self.data is None:
            return
        equipment = self.data.equipment
        for step in (
            self._add_new_equipment,
            self._sync_device_enabled,
            self._update_device_info,
            self._sync_unsupported_issues,
        ):
            try:
                step(equipment)
            except Exception:
                _LOGGER.exception("Failed to sync equipment (%s)", step.__name__)

    @callback
    def async_add_equipment_listener(self, listener: EquipmentListener) -> CALLBACK_TYPE:
        """Add a listener for equipment that needs entities.

        It is called at once with all supported equipment, then with equipment that appears in later refreshes.
        Equipment whose entities could not be created is passed to it again on the next refresh.
        """
        self._equipment_listeners[listener] = set()
        self._add_equipment_to(listener, self.data.equipment)

        @callback
        def remove_listener() -> None:
            self._equipment_listeners.pop(listener, None)

        return remove_listener

    @callback
    def async_forget_equipment(self, equipment_ids: set[str]) -> None:
        """Forget equipment whose device was deleted, so it is added again if it comes back."""
        for added in self._equipment_listeners.values():
            added -= equipment_ids

    def _add_new_equipment(self, equipment: dict[str, BrewCreatorEquipment]) -> None:
        for listener in list(self._equipment_listeners):
            self._add_equipment_to(listener, equipment)

    def _add_equipment_to(self, listener: EquipmentListener, equipment: dict[str, BrewCreatorEquipment]) -> None:
        """Pass the listener the supported equipment it has no entities for yet."""
        added = self._equipment_listeners[listener]
        new: list[BrewCreatorEquipment] = [
            e
            for equipment_id, e in equipment.items()
            if isinstance(e, SUPPORTED_EQUIPMENT) and equipment_id not in added
        ]
        if not new:
            return
        _LOGGER.debug("Adding equipment: %s", [e.name for e in new])
        try:
            failed = listener(new)
        except Exception:
            _LOGGER.exception("Failed to add entities for %s", [e.name for e in new])
            failed = {e.id for e in new}
        # Equipment whose entities failed is tried again on the next refresh
        added.update(e.id for e in new if e.id not in failed)

    def _sync_device_enabled(self, equipment: dict[str, BrewCreatorEquipment]) -> None:
        """Disable devices whose equipment is gone from BrewCreator, and enable them again if it comes back.

        Unsupported equipment counts as present. Nothing is disabled on an empty list, which is more likely a glitch
        than every device being deleted, and a device disabled by the user is never touched.
        """
        can_disable = bool(equipment)
        device_registry = dr.async_get(self.hass)
        for device in dr.async_entries_for_config_entry(device_registry, self.config_entry.entry_id):
            present = not equipment_ids(device).isdisjoint(equipment)
            if present and device.disabled_by == dr.DeviceEntryDisabler.INTEGRATION:
                _LOGGER.info("Enabling device %s, as it is back in BrewCreator", device_name(device))
                device_registry.async_update_device(device.id, disabled_by=None)
            elif not present and can_disable and device.disabled_by is None:
                _LOGGER.info(
                    "Disabling device %s, as it is no longer in BrewCreator",
                    device_name(device),
                )
                device_registry.async_update_device(device.id, disabled_by=dr.DeviceEntryDisabler.INTEGRATION)

    def _update_device_info(self, equipment: dict[str, BrewCreatorEquipment]) -> None:
        """Keep device name and firmware in sync. The registry only writes fields that changed."""
        device_registry = dr.async_get(self.hass)
        for e in equipment.values():
            if not isinstance(e, SUPPORTED_EQUIPMENT):
                continue
            try:
                info = device_info(e)
                device = find_device(device_registry, e.id, self.config_entry.entry_id)
                if device is not None:
                    device_registry.async_update_device(
                        device.id,
                        **{key: info[key] for key in SYNCED_DEVICE_FIELDS if key in info},
                    )
            except Exception:
                _LOGGER.exception("Failed to update device info of %s", e.name)

    def _sync_unsupported_issues(self, equipment_list: dict[str, BrewCreatorEquipment]) -> None:
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

    def diagnostics(self) -> dict[str, Any]:
        """Return the coordinator's state for diagnostics."""
        return {
            "last_update_success": self.last_update_success,
            "last_exception": repr(self.last_exception) if self.last_exception else None,
            "update_interval": str(self.update_interval),
            "known_equipment_ids": sorted(set().union(*self._equipment_listeners.values())),
            "devices_disabled_by_integration": sum(
                device.disabled_by == dr.DeviceEntryDisabler.INTEGRATION
                for device in dr.async_entries_for_config_entry(dr.async_get(self.hass), self.config_entry.entry_id)
            ),
        }

    async def close(self) -> None:
        await self._api.close()


def _op_type(message: dict[str, Any]) -> str | None:
    """Return the opType of a devicetwin message, such as Connected, Disconnected or telemetry, if it has one."""
    try:
        payload = json.loads(message["arguments"][0])
    except KeyError, IndexError, TypeError, ValueError:
        return None
    return payload.get("opType") if isinstance(payload, dict) else None
