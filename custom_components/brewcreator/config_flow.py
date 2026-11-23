"""Config flow for the BrewCreator integration."""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

import aiohttp
import probatio
from homeassistant.config_entries import ConfigEntry, ConfigEntryState, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_NAME, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
)

from .api import (
    TILT_IOT_HUB_ID,
    BrewCreatorAPI,
    BrewCreatorEquipment,
    BrewCreatorError,
    BrewCreatorInvalidCredentialsError,
    BrewCreatorRequestError,
    Ferminator,
    Graviator,
    GraviatorColor,
    Hydrometer,
    Tilt,
    TiltColor,
)
from .const import DOMAIN
from .device import unique_labels
from .token_store import BrewCreatorTokenStore

if TYPE_CHECKING:
    from .coordinator import BrewCreatorDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = probatio.Schema(
    {
        probatio.Required(CONF_USERNAME): str,
        probatio.Required(CONF_PASSWORD): str,
    }
)


CONF_SERIAL_NUMBER = "serial_number"
CONF_COLOR = "color"
CONF_EQUIPMENT = "equipment"

# The web app's limit for equipment names
MAX_NAME_LENGTH = 128

# A Ferminator or Graviator serial number, as printed with its QR code: 4, 3 and 3 letters or digits
SERIAL_NUMBER_PATTERN = re.compile(r"([a-z0-9]{4})-?([a-z0-9]{3})-?([a-z0-9]{3})", re.IGNORECASE)


def normalize_serial_number(value: str) -> str | None:
    """The serial number with dashes, as BrewCreator knows it, or None if it isn't one. The case is kept."""
    match = SERIAL_NUMBER_PATTERN.fullmatch("".join(value.split()))
    return "-".join(match.groups()) if match else None


def free_colors(
    hydrometer_class: type[Hydrometer], equipment: Mapping[str, BrewCreatorEquipment]
) -> list[TiltColor | GraviatorColor]:
    """The colours no other hydrometer of the type has. BrewCreator doesn't check this itself."""
    used = {e.color for e in equipment.values() if isinstance(e, hydrometer_class)}
    return [c for c in hydrometer_class.COLORS if c not in used]


class BrewCreatorConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for BrewCreator."""

    VERSION = 2

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return BrewCreatorOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                await BrewCreatorAPI(
                    user_input[CONF_USERNAME],
                    user_input[CONF_PASSWORD],
                    BrewCreatorTokenStore(self.hass),
                    async_get_clientsession(self.hass),
                ).verify_username_and_password()
            except aiohttp.ClientConnectionError:
                errors["base"] = "cannot_connect"
            except BrewCreatorInvalidCredentialsError:
                errors["base"] = "invalid_auth"
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(title="BrewCreator", data=user_input)

        return self.async_show_form(step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors)


class _StepError(Exception):
    """A step's error: the key of its message, and the message's placeholders."""

    def __init__(self, key: str, **placeholders: str) -> None:
        super().__init__(key)
        self.key = key
        self.placeholders = placeholders


class BrewCreatorOptionsFlow(OptionsFlow):
    """Add equipment to the BrewCreator account, or remove it, so the web app is only needed to create the account."""

    _added: Graviator | None = None
    """A Graviator registered by this flow that has no colour yet. It is removed again if the flow ends without one."""
    _added_name = ""
    _added_coordinator: BrewCreatorDataUpdateCoordinator | None = None
    """The coordinator that registered _added, kept as the config entry may be gone when the flow ends."""
    _remove_id: str | None = None

    @property
    def _coordinator(self) -> BrewCreatorDataUpdateCoordinator:
        return self.config_entry.runtime_data

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if self.config_entry.state is not ConfigEntryState.LOADED:
            return self.async_abort(reason="not_loaded")
        return self.async_show_menu(step_id="init", menu_options=["add_device", "remove_device"])

    async def async_step_add_device(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="add_device", menu_options=["add_by_serial_number", "add_tilt"])

    async def async_step_add_by_serial_number(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Add a Ferminator or Graviator. BrewCreator works out which from the serial number."""
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {}
        if user_input is not None:
            name = _name(user_input)
            serial_number = normalize_serial_number(user_input[CONF_SERIAL_NUMBER])
            if serial_number is None:
                errors[CONF_SERIAL_NUMBER] = "invalid_serial_number"
            elif self._in_account(serial_number):
                errors[CONF_SERIAL_NUMBER] = "already_in_account"
            elif len(name) > MAX_NAME_LENGTH:
                errors[CONF_NAME] = "name_too_long"
            else:
                try:
                    equipment = await self._async_register(serial_number, (Ferminator, Graviator))
                except _StepError as err:
                    errors["base"], placeholders = err.key, err.placeholders
                else:
                    if isinstance(equipment, Graviator):
                        if not free_colors(Graviator, self._coordinator.data.equipment):
                            await self._async_discard(equipment)
                            return self.async_abort(reason="no_free_graviator_color")
                        self._added, self._added_name = equipment, name
                        self._added_coordinator = self._coordinator
                        return await self.async_step_graviator_color()
                    return await self._async_name(equipment, name)
        schema = probatio.Schema(
            {
                probatio.Required(CONF_SERIAL_NUMBER): TextSelector(),
                probatio.Optional(CONF_NAME): TextSelector(),
            }
        )
        return self.async_show_form(
            step_id="add_by_serial_number",
            data_schema=self.add_suggested_values_to_schema(schema, user_input or {}),
            errors=errors,
            description_placeholders=placeholders,
        )

    async def async_step_graviator_color(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Choose the colour of the Graviator just registered, and name it."""
        graviator = self._added
        assert graviator is not None, "add_by_serial_number registers the Graviator first"
        colors = free_colors(Graviator, self._coordinator.data.equipment)
        errors: dict[str, str] = {}
        if user_input is not None:
            # Checked again, as another device may have taken the colour since the form was shown
            if (color := _color(Graviator, user_input[CONF_COLOR])) in colors:
                return await self._async_name(graviator, self._added_name, color)
            errors[CONF_COLOR] = "color_taken"
        schema = probatio.Schema({probatio.Required(CONF_COLOR): _color_selector(colors, "graviator_color")})
        return self.async_show_form(step_id="graviator_color", data_schema=schema, errors=errors)

    async def async_step_add_tilt(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Add a Tilt, which has no serial number, by its colour."""
        colors = free_colors(Tilt, self._coordinator.data.equipment)
        if not colors:
            return self.async_abort(reason="no_free_tilt_color")
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {}
        if user_input is not None:
            name = _name(user_input)
            color = _color(Tilt, user_input[CONF_COLOR])
            if color not in colors:
                # Another device has taken it since the form was shown
                errors[CONF_COLOR] = "color_taken"
            elif len(name) > MAX_NAME_LENGTH:
                errors[CONF_NAME] = "name_too_long"
            else:
                try:
                    tilt = await self._async_register(TILT_IOT_HUB_ID, (Tilt,))
                except _StepError as err:
                    errors["base"], placeholders = err.key, err.placeholders
                else:
                    return await self._async_name(tilt, name, color)
        schema = probatio.Schema(
            {
                probatio.Required(CONF_COLOR): _color_selector(colors, "tilt_color"),
                probatio.Optional(CONF_NAME): TextSelector(),
            }
        )
        return self.async_show_form(
            step_id="add_tilt",
            data_schema=self.add_suggested_values_to_schema(schema, user_input or {}),
            errors=errors,
            description_placeholders=placeholders,
        )

    def _in_account(self, serial_number: str) -> bool:
        return any(
            e.serial_number.casefold() == serial_number.casefold() for e in self._coordinator.data.equipment.values()
        )

    async def _async_register(
        self, iot_hub_id: str, expected: tuple[type[BrewCreatorEquipment], ...]
    ) -> BrewCreatorEquipment:
        """Register the equipment, removing it again unless it is of an expected type. Failures raise _StepError."""
        try:
            equipment = await self._coordinator.async_register_equipment(iot_hub_id)
        except BrewCreatorRequestError as err:
            if err.has_code("equipmentDoesNotExistOnIotHub", "incorrect_serial_number"):
                raise _StepError("serial_number_not_found") from err
            if err.has_code("EquipmentHaveAlreadyRegistered"):
                raise _StepError("already_registered") from err
            raise _StepError("cannot_add", error=str(err)) from err
        except BrewCreatorError as err:
            raise _StepError("cannot_add", error=str(err)) from err
        if not isinstance(equipment, expected):
            await self._async_discard(equipment)
            raise _StepError("unsupported_type", type=equipment.raw_type)
        return equipment

    async def _async_name(
        self, equipment: BrewCreatorEquipment, name: str, color: TiltColor | GraviatorColor | None = None
    ) -> ConfigFlowResult:
        """Name the newly registered equipment, as the default name if none is given, or remove it again."""
        self._added = None
        name = name or type(equipment).default_name(color)
        try:
            await equipment.set_name_and_color(name, color)
        except BrewCreatorError as err:
            await self._async_discard(equipment)
            return self.async_abort(reason="cannot_add", description_placeholders={"error": str(err)})
        await self._coordinator.async_refresh()
        return self.async_abort(reason="device_added", description_placeholders={"name": name})

    async def _async_discard(
        self, equipment: BrewCreatorEquipment, coordinator: BrewCreatorDataUpdateCoordinator | None = None
    ) -> None:
        """Remove equipment this flow registered but couldn't finish, as the web app's Cancel does.

        Failures are logged, as this also runs in the background once the flow is gone.
        """
        try:
            await (coordinator or self._coordinator).async_unregister_equipment(equipment)
        except Exception:
            _LOGGER.exception("Failed to remove %s again after adding it failed", equipment.serial_number)

    @callback
    def async_remove(self) -> None:
        """Remove a Graviator still without colour when the flow is closed, or ends in any other way."""
        if (graviator := self._added) is not None and (coordinator := self._added_coordinator) is not None:
            self._added = None
            self.hass.async_create_task(
                self._async_discard(graviator, coordinator), "brewcreator discard unfinished Graviator"
            )

    async def async_step_remove_device(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        equipment = self._coordinator.data.equipment
        if not equipment:
            return self.async_abort(reason="no_devices")
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input.get(CONF_EQUIPMENT) in equipment:
                self._remove_id = user_input[CONF_EQUIPMENT]
                return await self.async_step_remove_confirm()
            errors[CONF_EQUIPMENT] = "select_device"
        by_label = unique_labels(
            sorted(equipment.values(), key=lambda e: (e.name.casefold(), e.id)), lambda e: f"{e.name} ({e.raw_type})"
        )
        options = [SelectOptionDict(value=e.id, label=label) for label, e in by_label.items()]
        # Optional, as the frontend preselects the first option of a required field, here the first Ferminator
        schema = probatio.Schema(
            {
                probatio.Optional(CONF_EQUIPMENT): SelectSelector(
                    SelectSelectorConfig(options=options, mode=SelectSelectorMode.LIST)
                )
            }
        )
        return self.async_show_form(step_id="remove_device", data_schema=schema, errors=errors)

    async def async_step_remove_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        # Looked up again, so the links it removes first are as of the latest refresh
        equipment = self._coordinator.data.equipment.get(self._remove_id or "")
        if equipment is None:
            return self.async_abort(reason="already_removed")
        placeholders = {"name": equipment.name, "type": equipment.raw_type}
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                await self._coordinator.async_unregister_equipment(equipment)
            except BrewCreatorError as err:
                placeholders["error"] = str(err)
                errors["base"] = "cannot_remove"
            else:
                return self.async_abort(reason="device_removed", description_placeholders=placeholders)
        return self.async_show_form(
            step_id="remove_confirm",
            data_schema=probatio.Schema({}),
            errors=errors,
            description_placeholders=placeholders,
        )


def _name(user_input: Mapping[str, Any]) -> str:
    """The name given, with whitespace collapsed, or an empty string if none was given."""
    return " ".join(user_input.get(CONF_NAME, "").split())


def _color(hydrometer_class: type[Hydrometer], key: str) -> TiltColor | GraviatorColor:
    return {c.key: c for c in hydrometer_class.COLORS}[key]


def _color_selector(colors: list[TiltColor | GraviatorColor], translation_key: str) -> SelectSelector:
    return SelectSelector(
        SelectSelectorConfig(
            options=[c.key for c in colors], translation_key=translation_key, mode=SelectSelectorMode.DROPDOWN
        )
    )
