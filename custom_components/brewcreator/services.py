"""Actions of the BrewCreator integration."""

from collections.abc import Mapping
from typing import Any

import probatio
from homeassistant.const import ATTR_DEVICE_ID
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr

from .api import BATCH_INFO_EQUIPMENT, BatchField, BrewCreatorEquipment, FermentationType, Ferminator, Graviator
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
ATTR_CLEAR = "clear"

# The action's fields besides the device, of which at least one must be given
BATCH_FIELDS = (*(f.key for f in BatchField), ATTR_CLEAR)

# The fields the clear field can name. The batch name can't be cleared, as BrewCreator equipment without one has no
# batch info.
CLEARABLE_FIELDS = {f.key: f for f in BatchField if f.unset_value is not None}

# The limits of BrewCreator's batch info form
MAX_TEXT_LENGTH = 128
MIN_GRAVITY = 0.98
MAX_GRAVITY = 1.999
MAX_INTEGER = 9999

FERMENTATION_TYPES = {t.key: t for t in FermentationType}

TEXT = probatio.All(cv.string, probatio.Length(min=1, max=MAX_TEXT_LENGTH))
GRAVITY = probatio.All(probatio.Coerce(float), probatio.Range(min=MIN_GRAVITY, max=MAX_GRAVITY))
POSITIVE_INTEGER = probatio.All(probatio.Coerce(int), probatio.Range(min=1, max=MAX_INTEGER))
CLEAR = probatio.All(cv.ensure_list, probatio.Length(min=1), [probatio.In(list(CLEARABLE_FIELDS))])

SET_BATCH_INFO_SCHEMA = probatio.All(
    probatio.Schema(
        {
            probatio.Required(ATTR_DEVICE_ID): cv.string,
            probatio.Optional(ATTR_BATCH_NAME): TEXT,
            probatio.Optional(ATTR_OWNER): TEXT,
            probatio.Optional(ATTR_BEER_STYLE): TEXT,
            probatio.Optional(ATTR_ORIGINAL_GRAVITY): GRAVITY,
            probatio.Optional(ATTR_FINAL_GRAVITY): GRAVITY,
            probatio.Optional(ATTR_EBC): POSITIVE_INTEGER,
            probatio.Optional(ATTR_IBU): POSITIVE_INTEGER,
            probatio.Optional(ATTR_VOLUME): POSITIVE_INTEGER,
            probatio.Optional(ATTR_FERMENTATION_TYPE): probatio.All(
                probatio.In(list(FERMENTATION_TYPES)), FERMENTATION_TYPES.__getitem__
            ),
            probatio.Optional(ATTR_STARTED): cv.boolean,
            probatio.Optional(ATTR_CLEAR): CLEAR,
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
    """Change or clear the given batch info fields of a Ferminator or an unlinked Graviator, leaving the others."""
    data = call.data
    _validate_not_set_and_cleared(data)
    coordinator, equipment = _find_batch_equipment(call.hass, data[ATTR_DEVICE_ID])
    changes = {field: data[field.key] for field in BatchField if field.key in data}
    changes.update({CLEARABLE_FIELDS[key]: None for key in data.get(ATTR_CLEAR, ())})
    _validate_gravities(equipment, changes)
    # Refreshed at once, so the batch sensors show the new values when the action returns
    await coordinator.async_send(
        equipment.set_batch_info(changes),
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


def _validate_not_set_and_cleared(data: Mapping[str, Any]) -> None:
    """Refuse a field that is both given a value and cleared."""
    both = [field for field in data.get(ATTR_CLEAR, ()) if field in data]
    if both:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="set_and_cleared",
            translation_placeholders={"fields": ", ".join(both)},
        )


def _validate_gravities(equipment: BrewCreatorEquipment, changes: Mapping[BatchField, Any]) -> None:
    """Require the final gravity to be below the original gravity, like BrewCreator's batch info form.

    A gravity that isn't changed is taken from the current batch info, and one that is cleared isn't compared.
    """
    batch_info = equipment.batch_info
    og = changes.get(BatchField.ORIGINAL_GRAVITY, batch_info.og if batch_info else None)
    fg = changes.get(BatchField.FINAL_GRAVITY, batch_info.fg if batch_info else None)
    if og is not None and fg is not None and fg >= og:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="final_gravity_not_below_original",
            translation_placeholders={"og": f"{og:.3f}", "fg": f"{fg:.3f}"},
        )
