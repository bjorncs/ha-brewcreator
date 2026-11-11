"""Actions of the BrewCreator integration."""

import voluptuous as vol
from homeassistant.const import ATTR_DEVICE_ID
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr

from .api import BATCH_INFO_EQUIPMENT, BrewCreatorEquipment, FermentationType, Ferminator, Graviator
from .const import DOMAIN
from .coordinator import BrewCreatorDataUpdateCoordinator
from .device import equipment_ids

SERVICE_SET_BATCH_INFO = "set_batch_info"

ATTR_BATCH_NAME = "batch_name"
ATTR_OWNER = "owner"
ATTR_BEER_STYLE = "beer_style"
ATTR_ORIGINAL_GRAVITY = "original_gravity"
ATTR_FINAL_GRAVITY = "final_gravity"
ATTR_EBC = "ebc"
ATTR_IBU = "ibu"
ATTR_VOLUME = "volume"
ATTR_FERMENTATION_TYPE = "fermentation_type"
ATTR_STARTED = "started"

BATCH_FIELDS = (
    ATTR_BATCH_NAME,
    ATTR_OWNER,
    ATTR_BEER_STYLE,
    ATTR_ORIGINAL_GRAVITY,
    ATTR_FINAL_GRAVITY,
    ATTR_EBC,
    ATTR_IBU,
    ATTR_VOLUME,
    ATTR_FERMENTATION_TYPE,
    ATTR_STARTED,
)

# The limits of BrewCreator's batch info form
MAX_TEXT_LENGTH = 128
MIN_GRAVITY = 0.98
MAX_GRAVITY = 1.999
MAX_INTEGER = 9999

FERMENTATION_TYPES = {t.key: t for t in FermentationType}

TEXT = vol.All(cv.string, vol.Length(min=1, max=MAX_TEXT_LENGTH))
GRAVITY = vol.All(vol.Coerce(float), vol.Range(min=MIN_GRAVITY, max=MAX_GRAVITY))
POSITIVE_INTEGER = vol.All(vol.Coerce(int), vol.Range(min=1, max=MAX_INTEGER))

SET_BATCH_INFO_SCHEMA = vol.All(
    vol.Schema(
        {
            vol.Required(ATTR_DEVICE_ID): cv.string,
            vol.Optional(ATTR_BATCH_NAME): TEXT,
            vol.Optional(ATTR_OWNER): TEXT,
            vol.Optional(ATTR_BEER_STYLE): TEXT,
            vol.Optional(ATTR_ORIGINAL_GRAVITY): GRAVITY,
            vol.Optional(ATTR_FINAL_GRAVITY): GRAVITY,
            vol.Optional(ATTR_EBC): POSITIVE_INTEGER,
            vol.Optional(ATTR_IBU): POSITIVE_INTEGER,
            vol.Optional(ATTR_VOLUME): POSITIVE_INTEGER,
            vol.Optional(ATTR_FERMENTATION_TYPE): vol.In(list(FERMENTATION_TYPES)),
            vol.Optional(ATTR_STARTED): cv.boolean,
        }
    ),
    cv.has_at_least_one_key(*BATCH_FIELDS),
)


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the integration's actions."""
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_BATCH_INFO,
        _async_set_batch_info,
        schema=SET_BATCH_INFO_SCHEMA,
    )


async def _async_set_batch_info(call: ServiceCall) -> None:
    """Change the given batch info fields of a Ferminator or an unlinked Graviator, leaving the others as they are."""
    coordinator, equipment = _find_batch_equipment(call.hass, call.data[ATTR_DEVICE_ID])
    data = call.data
    _validate_gravities(equipment, data.get(ATTR_ORIGINAL_GRAVITY), data.get(ATTR_FINAL_GRAVITY))
    fermentation_type = data.get(ATTR_FERMENTATION_TYPE)
    # Refreshed at once, so the batch sensors show the new values when the action returns
    await coordinator.async_send(
        equipment.set_batch_info(
            brew_name=data.get(ATTR_BATCH_NAME),
            owner=data.get(ATTR_OWNER),
            beer_style=data.get(ATTR_BEER_STYLE),
            og=data.get(ATTR_ORIGINAL_GRAVITY),
            fg=data.get(ATTR_FINAL_GRAVITY),
            ebc=data.get(ATTR_EBC),
            ibu=data.get(ATTR_IBU),
            volume=data.get(ATTR_VOLUME),
            fermentation_type=FERMENTATION_TYPES[fermentation_type] if fermentation_type is not None else None,
            is_logging_data=data.get(ATTR_STARTED),
        ),
        "update_failed",
        equipment.name,
    )


def _find_batch_equipment(
    hass: HomeAssistant, device_id: str
) -> tuple[BrewCreatorDataUpdateCoordinator, Ferminator | Graviator]:
    """Return the Ferminator or unlinked Graviator of a device and the coordinator of its loaded config entry.

    A linked Graviator is refused, as BrewCreator copies the Ferminator's batch info to it.
    """
    device = dr.async_get(hass).async_get(device_id)
    if device is not None:
        for entry in hass.config_entries.async_loaded_entries(DOMAIN):
            coordinator: BrewCreatorDataUpdateCoordinator = entry.runtime_data
            for equipment_id in equipment_ids(device):
                equipment = coordinator.data.equipment.get(equipment_id)
                if not isinstance(equipment, BATCH_INFO_EQUIPMENT):
                    continue
                owner = equipment.batch_owner
                if owner is not equipment:
                    raise ServiceValidationError(
                        translation_domain=DOMAIN,
                        translation_key="linked_to_ferminator",
                        translation_placeholders={"name": equipment.name, "ferminator": owner.name},
                    )
                return coordinator, equipment
    raise ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key="no_batch_info",
        translation_placeholders={"device_id": device_id},
    )


def _validate_gravities(equipment: BrewCreatorEquipment, og: float | None, fg: float | None) -> None:
    """Require the final gravity to be below the original gravity, like BrewCreator's batch info form.

    A gravity that isn't given is taken from the current batch info.
    """
    batch_info = equipment.batch_info
    if og is None and batch_info is not None:
        og = batch_info.og
    if fg is None and batch_info is not None:
        fg = batch_info.fg
    if og is not None and fg is not None and fg >= og:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="final_gravity_not_below_original",
            translation_placeholders={"og": f"{og:.3f}", "fg": f"{fg:.3f}"},
        )
