"""Intents of the BrewCreator integration.

Home Assistant offers every intent with a description and slot schema as a tool to LLM conversation agents through
the Assist API, and to MCP clients through the MCP Server integration. These intents give those agents what the
built-in intents don't: a summary of a brew, the batch info and the hydrometer linked to a Ferminator.

An intent only sees equipment whose device has an entity exposed to the assistant asking, such as the Ferminator's
climate entity.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.components.homeassistant.exposed_entities import async_should_expose
from homeassistant.const import ATTR_DEVICE_ID
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import (
    device_registry as dr,
    entity_registry as er,
    intent,
)
from homeassistant.util import dt as dt_util

from .api import BrewCreatorEquipment, BrewCreatorError, Ferminator, FerminatorMode, Hydrometer, linked_ferminator
from .climate import FAN_SPEEDS
from .const import DOMAIN
from .coordinator import BrewCreatorDataUpdateCoordinator
from .device import device_name, find_device
from .select import NO_HYDROMETER, hydrometers_by_option
from .services import (
    ATTR_BATCH_NAME,
    ATTR_BEER_STYLE,
    ATTR_EBC,
    ATTR_FERMENTATION_TYPE,
    ATTR_FINAL_GRAVITY,
    ATTR_IBU,
    ATTR_ORIGINAL_GRAVITY,
    ATTR_OWNER,
    ATTR_STARTED,
    ATTR_VOLUME,
    BATCH_FIELDS,
    FERMENTATION_TYPES,
    MAX_TEXT_LENGTH,
    GRAVITY,
    POSITIVE_INTEGER,
    SERVICE_SET_BATCH_INFO,
)

INTENT_GET_FERMENTATION_STATUS = "BrewCreatorGetFermentationStatus"
INTENT_SET_BATCH_INFO = "BrewCreatorSetBatchInfo"
INTENT_LINK_HYDROMETER = "BrewCreatorLinkHydrometer"

SLOT_NAME = "name"
SLOT_HYDROMETER = "hydrometer"

# Words an agent may use for unlinking the hydrometer
_NO_HYDROMETER_WORDS = {NO_HYDROMETER, "nothing", "no hydrometer", "unlink", "disconnect"}

# Like services.TEXT, but with str instead of cv.string, which doesn't convert to an OpenAPI schema for the LLM tools
_TEXT = vol.All(str, vol.Length(min=1, max=MAX_TEXT_LENGTH))


async def async_setup_intents(hass: HomeAssistant) -> None:
    """Register the integration's intents. Called by the intent integration, which loads intent platforms."""
    intent.async_register(hass, GetFermentationStatusIntentHandler())
    intent.async_register(hass, SetBatchInfoIntentHandler())
    intent.async_register(hass, LinkHydrometerIntentHandler())


@dataclass(frozen=True)
class _Target:
    """A piece of equipment with its coordinator and device."""

    coordinator: BrewCreatorDataUpdateCoordinator
    equipment: BrewCreatorEquipment
    device: dr.DeviceEntry

    @property
    def name(self) -> str:
        return device_name(self.device) or self.equipment.name

    @property
    def names(self) -> set[str]:
        """Names the equipment can be referred to by, lower case."""
        return {" ".join(n.casefold().split()) for n in (device_name(self.device), self.equipment.name) if n}


def _exposed_targets(hass: HomeAssistant, assistant: str | None) -> list[_Target]:
    """Return the Ferminators and hydrometers whose device has an entity exposed to the assistant, by name.

    Without an assistant, as when an intent is handled through the intent API, all of them are returned.
    """
    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)
    targets = []
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        coordinator: BrewCreatorDataUpdateCoordinator = entry.runtime_data
        for equipment in coordinator.data.equipment.values():
            if not isinstance(equipment, (Ferminator, Hydrometer)):
                continue
            device = find_device(device_registry, equipment.id, entry.entry_id)
            if device is None or device.disabled_by is not None:
                continue
            if assistant is not None and not any(
                async_should_expose(hass, assistant, entity.entity_id)
                for entity in er.async_entries_for_device(entity_registry, device.id)
            ):
                continue
            targets.append(_Target(coordinator, equipment, device))
    return sorted(targets, key=lambda t: t.name.casefold())


def _of_type(targets: list[_Target], equipment_type: type[BrewCreatorEquipment]) -> list[_Target]:
    return [t for t in targets if isinstance(t.equipment, equipment_type)]


def _match_target(
    targets: list[_Target], name: str | None, kind: str, default: Iterable[_Target] = ()
) -> _Target:
    """Return the target referred to by name, or the only default target when no name is given.

    A name matches a device's name or BrewCreator name regardless of case, or else whole words of a single one.
    """
    if not targets:
        raise intent.IntentHandleError(
            f"No {kind} is exposed to this assistant. Expose it under Settings > Voice assistants > Expose."
        )
    if name is None:
        default = list(default)
        if len(default) == 1:
            return default[0]
        raise intent.IntentHandleError(f"Name the {kind}: {_names(targets)}")
    wanted = " ".join(name.casefold().split())
    matches = [t for t in targets if wanted in t.names]
    if not matches:
        matches = [t for t in targets if any(f" {wanted} " in f" {n} " for n in t.names)]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise intent.IntentHandleError(f"There is no {kind} named {name!r}. The {kind}s are: {_names(targets)}")
    raise intent.IntentHandleError(f"{name!r} matches several {kind}s: {_names(matches)}")


def _names(targets: Iterable[_Target]) -> str:
    return ", ".join(t.name for t in targets)


def _slot_value(slots: dict[str, Any], name: str) -> Any:
    slot = slots.get(name)
    return None if slot is None else slot["value"]


class GetFermentationStatusIntentHandler(intent.IntentHandler):
    """Report the batch, temperatures and gravity of a brew in a Ferminator, or of a hydrometer."""

    intent_type = INTENT_GET_FERMENTATION_STATUS
    description = (
        "Gets the status of a brew in a BrewCreator Ferminator fermentation chamber, or of a hydrometer "
        "(Tilt, Graviator): batch info, temperatures, whether temperature regulation is on, fan speed, specific "
        "gravity, ABV and how long it has fermented. "
        "Use this for any question about beer, brewing or fermentation."
    )
    platforms = None

    @property
    def slot_schema(self) -> dict:
        return {
            vol.Optional(
                SLOT_NAME,
                description="Name of the Ferminator or hydrometer. Can be left out when there is only one Ferminator.",
            ): _TEXT,
        }

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        slots = self.async_validate_slots(intent_obj.slots)
        targets = _exposed_targets(intent_obj.hass, intent_obj.assistant)
        ferminators = _of_type(targets, Ferminator)
        target = _match_target(
            targets,
            _slot_value(slots, SLOT_NAME),
            "Ferminator or hydrometer",
            default=ferminators or _of_type(targets, Hydrometer),
        )
        # Linked equipment is only included when it is exposed too
        exposed_ids = {t.equipment.id for t in targets}
        if isinstance(target.equipment, Ferminator):
            ferminator = target.equipment
            hydrometer = _reporting_hydrometer(ferminator, exposed_ids)
        else:
            hydrometer = target.equipment
            ferminator = linked_ferminator(hydrometer, target.coordinator.data.equipment)
            if ferminator is not None and ferminator.id not in exposed_ids:
                ferminator = None

        status: dict[str, Any] = {}
        if ferminator is not None:
            status["ferminator"] = _ferminator_status(ferminator)
            status["batch"] = _batch_status(ferminator)
        if hydrometer is not None:
            status["hydrometer"] = _hydrometer_status(hydrometer)

        response = intent_obj.create_response()
        response.async_set_speech(_speech(target.name, status))
        response.async_set_speech_slots(status)
        return response


def _reporting_hydrometer(ferminator: Ferminator, exposed_ids: set[str]) -> Hydrometer | None:
    """Return the exposed linked hydrometer to report, preferring the one the Ferminator regulates on."""
    linked = [h for h in ferminator.linked_hydrometers if h.id in exposed_ids]
    active = ferminator.active_hydrometer
    if active is not None and active in linked:
        return active
    return linked[0] if linked else None


def _ferminator_status(ferminator: Ferminator) -> dict[str, Any]:
    status = {
        "name": ferminator.name,
        "connected": ferminator.is_connected,
        "temperature_c": ferminator.actual_temperature,
        "built_in_probe_temperature_c": ferminator.actual_temperature_builtin_probe,
        "target_temperature_c": ferminator.target_temperature,
        "mode": ferminator.mode.name.lower() if ferminator.mode is not None else None,
        # Like the climate entity, which is off while the Ferminator is ready
        "temperature_regulation": None if ferminator.mode is None else ferminator.mode != FerminatorMode.READY,
        "fan_speed": FAN_SPEEDS.get(ferminator.fan_speed),
    }
    return {k: v for k, v in status.items() if v is not None}


def _batch_status(ferminator: Ferminator) -> dict[str, Any]:
    batch = ferminator.batch_info
    if batch is None:
        return {}
    status: dict[str, Any] = {
        "name": batch.brew_name or None,
        "beer_style": batch.beer_style or None,
        "owner": batch.owner or None,
        "started": ferminator.is_logging_data,
        # An original gravity of 0 means it isn't set
        "original_gravity": batch.og or None,
        "final_gravity": batch.fg or None,
        "ebc": batch.ebc or None,
        "ibu": batch.ibu or None,
        "volume_l": batch.volume or None,
        "fermentation_type": batch.fermentation_type.name.lower()
        if batch.fermentation_type is not None
        else None,
    }
    if (brew_date := batch.brew_date) is not None:
        if brew_date.tzinfo is None:
            brew_date = brew_date.replace(tzinfo=dt_util.UTC)
        status["brew_start_date"] = brew_date.isoformat()
        status["days_since_brew_start"] = (dt_util.utcnow() - brew_date).days
    return {k: v for k, v in status.items() if v is not None}


def _hydrometer_status(hydrometer: Hydrometer) -> dict[str, Any]:
    last_reading = hydrometer.last_activity_time
    status = {
        "name": hydrometer.name,
        "specific_gravity": hydrometer.specific_gravity,
        "temperature_c": hydrometer.actual_temperature,
        "abv_percent": hydrometer.abv,
        "last_reading": last_reading.isoformat() if last_reading is not None else None,
    }
    return {k: v for k, v in status.items() if v is not None}


def _speech(name: str, status: dict[str, Any]) -> str:
    """Return a short spoken summary of the status, starting with the name."""
    batch = status.get("batch", {})
    ferminator = status.get("ferminator", {})
    hydrometer = status.get("hydrometer", {})
    lead = f"{name} is brewing {batch['name']}" if batch.get("name") else name
    if "temperature_c" in ferminator:
        lead += f" at {ferminator['temperature_c']:.1f} °C"
        if "target_temperature_c" in ferminator:
            lead += f", target {ferminator['target_temperature_c']:.1f} °C"
    elif lead == name and "temperature_c" in hydrometer:
        lead += f" is at {hydrometer['temperature_c']:.1f} °C"
    parts = [lead]
    if "specific_gravity" in hydrometer:
        parts.append(f"gravity {hydrometer['specific_gravity']:.3f}")
    if "abv_percent" in hydrometer:
        parts.append(f"{hydrometer['abv_percent']:.1f} % ABV")
    if "days_since_brew_start" in batch:
        parts.append(f"day {batch['days_since_brew_start']}")
    if len(parts) == 1 and lead == name:
        return f"No readings for {name}."
    return ", ".join(parts) + "."


class SetBatchInfoIntentHandler(intent.IntentHandler):
    """Change the batch info of a Ferminator, through the set_batch_info action."""

    intent_type = INTENT_SET_BATCH_INFO
    description = (
        "Changes the batch info of a BrewCreator Ferminator: batch name, owner, beer style, gravities, colour, "
        "bitterness, volume, fermentation type, and whether BrewCreator logs the brew. Only the given fields are "
        "changed. Give at least one. It doesn't control the Ferminator itself: to turn temperature regulation on or "
        "off, or change the target temperature or fan speed, control the Ferminator's Temperature Control climate "
        "entity, for example with HassTurnOn, HassTurnOff or HassClimateSetTemperature."
    )
    platforms = None

    @property
    def slot_schema(self) -> dict:
        return {
            vol.Optional(
                SLOT_NAME,
                description="Name of the Ferminator. Can be left out when there is only one.",
            ): _TEXT,
            vol.Optional(ATTR_BATCH_NAME, description="Name of the batch."): _TEXT,
            vol.Optional(ATTR_OWNER, description="The brewer of the batch."): _TEXT,
            vol.Optional(
                ATTR_BEER_STYLE, description="Style of the beer, such as IPA or Scottish Export."
            ): _TEXT,
            vol.Optional(
                ATTR_ORIGINAL_GRAVITY,
                description="Specific gravity before fermentation, such as 1.058. Must be above the final gravity.",
            ): GRAVITY,
            vol.Optional(
                ATTR_FINAL_GRAVITY,
                description="Expected or measured specific gravity after fermentation, such as 1.012. "
                "Must be below the original gravity.",
            ): GRAVITY,
            vol.Optional(ATTR_EBC, description="Colour of the beer in EBC."): POSITIVE_INTEGER,
            vol.Optional(ATTR_IBU, description="Bitterness in IBU."): POSITIVE_INTEGER,
            vol.Optional(ATTR_VOLUME, description="Batch volume in litres."): POSITIVE_INTEGER,
            vol.Optional(
                ATTR_FERMENTATION_TYPE,
                description="top for top fermented (ale), bottom for bottom fermented (lager).",
            ): vol.In(list(FERMENTATION_TYPES)),
            vol.Optional(
                ATTR_STARTED,
                description="true to start logging the brew's readings in BrewCreator, false to stop logging. "
                "Doesn't turn temperature regulation on or off.",
            ): bool,
        }

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        slots = self.async_validate_slots(intent_obj.slots)
        ferminators = _of_type(_exposed_targets(intent_obj.hass, intent_obj.assistant), Ferminator)
        target = _match_target(
            ferminators, _slot_value(slots, SLOT_NAME), "Ferminator", default=ferminators
        )
        fields = {f: _slot_value(slots, f) for f in BATCH_FIELDS if f in slots}
        if not fields:
            raise intent.IntentHandleError(f"Give at least one of: {', '.join(BATCH_FIELDS)}")
        try:
            await intent_obj.hass.services.async_call(
                DOMAIN,
                SERVICE_SET_BATCH_INFO,
                {ATTR_DEVICE_ID: target.device.id, **fields},
                blocking=True,
                context=intent_obj.context,
            )
        except (HomeAssistantError, vol.Invalid) as err:
            raise intent.IntentHandleError(str(err)) from err

        response = intent_obj.create_response()
        response.async_set_speech(f"Changed the batch info of {target.name}.")
        response.async_set_speech_slots({"ferminator": target.name, "changed": fields})
        return response


class LinkHydrometerIntentHandler(intent.IntentHandler):
    """Change the hydrometer linked to a Ferminator, choosing from the options of its Hydrometer select."""

    intent_type = INTENT_LINK_HYDROMETER
    description = (
        "Links a hydrometer (Tilt, Graviator) to a BrewCreator Ferminator, replacing the one linked now, "
        f"or unlinks it with hydrometer {NO_HYDROMETER!r}. The Ferminator then regulates on, and logs, that "
        "hydrometer's readings."
    )
    platforms = None

    @property
    def slot_schema(self) -> dict:
        return {
            vol.Optional(
                SLOT_NAME,
                description="Name of the Ferminator. Can be left out when there is only one.",
            ): _TEXT,
            vol.Required(
                SLOT_HYDROMETER,
                description=f"Name of the hydrometer to link, or {NO_HYDROMETER!r} to unlink the current one.",
            ): _TEXT,
        }

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        slots = self.async_validate_slots(intent_obj.slots)
        hass = intent_obj.hass
        targets = _exposed_targets(hass, intent_obj.assistant)
        ferminators = _of_type(targets, Ferminator)
        target = _match_target(
            ferminators, _slot_value(slots, SLOT_NAME), "Ferminator", default=ferminators
        )
        ferminator: Ferminator = target.equipment
        # Only exposed hydrometers can be linked. The options are the select's, which may include an ID suffix.
        exposed_ids = {t.equipment.id for t in targets}
        hydrometers = {
            option: h
            for option, h in hydrometers_by_option(ferminator, target.coordinator.data.equipment).items()
            if h.id in exposed_ids
        }
        option = _match_option([NO_HYDROMETER, *hydrometers], _slot_value(slots, SLOT_HYDROMETER))

        # Linked directly rather than through the select, which may be disabled
        try:
            await ferminator.link_hydrometer(None if option == NO_HYDROMETER else hydrometers[option])
        except (BrewCreatorError, aiohttp.ClientError, TimeoutError) as err:
            raise intent.IntentHandleError(
                f"Failed to change the hydrometer linked to {target.name}: {err}"
            ) from err
        await target.coordinator.async_refresh()

        response = intent_obj.create_response()
        if option == NO_HYDROMETER:
            response.async_set_speech(f"Unlinked the hydrometer from {target.name}.")
        else:
            response.async_set_speech(f"Linked {option} to {target.name}.")
        response.async_set_speech_slots({"ferminator": target.name, "hydrometer": option})
        return response


def _match_option(options: list[str], wanted: str) -> str:
    """Return the Hydrometer select option referred to, regardless of case, or by the start of a single one."""
    normalized = wanted.strip().casefold()
    if normalized in _NO_HYDROMETER_WORDS:
        return NO_HYDROMETER
    for option in options:
        if option.casefold() == normalized:
            return option
    matches = [o for o in options[1:] if o.casefold().startswith(normalized)]
    if len(matches) == 1:
        return matches[0]
    linkable = ", ".join(options[1:]) or "none"
    raise intent.IntentHandleError(
        f"No single hydrometer named {wanted!r} can be linked. The linkable hydrometers are: {linkable}"
    )
