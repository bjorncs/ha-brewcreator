"""The BrewCreator integration."""

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    config_validation as cv,
    device_registry as dr,
    entity_registry as er,
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import BrewCreatorAPI
from .const import DOMAIN
from .coordinator import BrewCreatorDataUpdateCoordinator
from .device import TILT_MANUFACTURER, device_name, equipment_ids
from .entity import equipment_id_from_unique_id
from .services import async_setup_services
from .token_store import BrewCreatorTokenStore

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.CLIMATE,
    Platform.SENSOR,
]

# Platforms of the batch controls replaced by the set_batch_info action in 1.0.0
REMOVED_PLATFORMS = {Platform.NUMBER, Platform.SWITCH, Platform.TEXT}

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the integration's actions."""
    async_setup_services(hass)
    return True


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry[BrewCreatorDataUpdateCoordinator]
) -> bool:
    """Set up devices from BrewCreator such as Ferminator, Tilt and Graviator"""
    api = BrewCreatorAPI(
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
        BrewCreatorTokenStore(hass),
        async_get_clientsession(hass),
    )
    coordinator = BrewCreatorDataUpdateCoordinator(hass, api, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: ConfigEntry[BrewCreatorDataUpdateCoordinator]
) -> bool:
    """Unload a config entry."""
    coordinator = entry.runtime_data
    await coordinator.close()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_config_entry_device(
    hass: HomeAssistant,
    entry: ConfigEntry[BrewCreatorDataUpdateCoordinator],
    device_entry: dr.DeviceEntry,
) -> bool:
    """Allow removing a device by hand only when it is gone from BrewCreator."""
    coordinator = entry.runtime_data
    ids = equipment_ids(device_entry)
    if ids & coordinator.data.equipment.keys():
        return False
    coordinator.async_forget_equipment(ids)
    return True


async def async_migrate_entry(
    hass: HomeAssistant, entry: ConfigEntry[BrewCreatorDataUpdateCoordinator]
) -> bool:
    """Migrate an old config entry.

    Version 2 identifies devices by equipment ID instead of serial number. The equipment ID is taken from the
    unique IDs of each device's entities, so devices, entities, entity IDs and history stay the same.

    Version 2 also replaces the batch options flow and the batch switch, number and text entities with the
    set_batch_info action and read-only sensors, so those entities and the stored options are removed.
    """
    if entry.version > 2:
        # Downgraded from a future version
        return False
    if entry.version == 1:
        _migrate_device_identifiers(hass, entry)
        _remove_batch_controls(hass, entry)
        hass.config_entries.async_update_entry(entry, version=2, options={})
        _LOGGER.info("Migrated BrewCreator config entry to version 2")
    return True


def _migrate_device_identifiers(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Identify devices by equipment ID, read from the unique IDs of their entities."""
    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)
    # An equipment ID can identify only one device, so of devices with entities of the same equipment, the first
    # one keeps it
    claimed: set[str] = set()
    for device in dr.async_entries_for_config_entry(device_registry, entry.entry_id):
        ids = {
            equipment_id
            for entity in er.async_entries_for_device(
                entity_registry, device.id, include_disabled_entities=True
            )
            if entity.config_entry_id == entry.entry_id
            and (equipment_id := equipment_id_from_unique_id(entity.unique_id))
        }
        if shared := ids & claimed:
            _LOGGER.warning(
                "Device %s has entities of equipment %s, which another device keeps",
                device_name(device),
                ", ".join(sorted(shared)),
            )
            ids -= shared
        claimed |= ids
        changes = {}
        if ids:
            changes["new_identifiers"] = {(DOMAIN, e) for e in ids}
        else:
            _LOGGER.warning(
                "Device %s has no entities of its own, so its identifiers can't be migrated",
                device_name(device),
            )
        if device.manufacturer == TILT_MANUFACTURER and device.serial_number is not None:
            # A Tilt's serial number is an ID generated by BrewCreator
            changes["serial_number"] = None
        if changes:
            device_registry.async_update_device(device.id, **changes)


def _remove_batch_controls(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove the batch switch, number and text entities, which the integration no longer provides."""
    entity_registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(entity_registry, entry.entry_id):
        if entity.domain in REMOVED_PLATFORMS:
            entity_registry.async_remove(entity.entity_id)
