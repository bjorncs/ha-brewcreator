from homeassistant.helpers.entity import DeviceInfo

from .api import Ferminator, Tilt
from .const import DOMAIN


def ferminator_device_info(ferminator: Ferminator) -> DeviceInfo:
    """Return device info for Ferminator."""
    return DeviceInfo(
        identifiers={(DOMAIN, ferminator.serial_number)},
        manufacturer="Brewolution",
        serial_number=ferminator.serial_number,
        model=ferminator.equipment_type.value,
        name=ferminator.name,
        sw_version=ferminator.sw_version,
        hw_version=ferminator.hw_version,
    )


def tilt_device_info(tilt: Tilt) -> DeviceInfo:
    """Return device info for Tilt."""
    return DeviceInfo(
        identifiers={(DOMAIN, tilt.serial_number)},
        manufacturer="Tilt",
        serial_number=tilt.serial_number,
        model=tilt.color.name,
        name=tilt.name,
    )
