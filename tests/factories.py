"""Builders for BrewCreator equipment and Home Assistant registry entries used across the tests."""

import json
from functools import partial
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import probatio
from homeassistant.helpers import intent, llm

from custom_components.brewcreator.api import (
    BrewCreatorEquipment,
    Ferminator,
    Graviator,
    Hydrometer,
    Tilt,
    UnsupportedEquipment,
    resolve_links,
)
from custom_components.brewcreator.coordinator import (
    BrewCreatorData,
    BrewCreatorDataUpdateCoordinator,
)
from custom_components.brewcreator.sensor import (
    BATCH_INFO_SENSORS,
    FERMINATOR_SENSORS,
    HYDROMETER_SENSORS,
    BatchInfoSensor,
    BrewCreatorSensor,
)

BATCH = {
    "brewName": "Christmas Miracle",
    "owner": "bjorncs",
    "brewDate": "2026-11-08T10:00:00+00:00",
    "ebc": 34,
    "ibu": 26,
    "volume": 21,
    "fermented": "Top",
    "og": 1.058,
    "fg": 1.015,
    "beerStyle": "Scottish Export",
    "isLoggingData": True,
}
"""Batch info fields of equipment JSON, for a started brew."""


def coordinator(*equipment: BrewCreatorEquipment, last_update_success: bool = True) -> MagicMock:
    """A coordinator holding the equipment with links resolved, as of a refresh that succeeded unless told otherwise.

    Updates are sent with the coordinator's own async_send, and async_refresh is an AsyncMock.
    """
    coordinator = MagicMock()
    coordinator.data = BrewCreatorData(equipment={e.id: e for e in equipment})
    resolve_links(coordinator.data.equipment)
    coordinator.last_update_success = last_update_success
    coordinator.async_refresh = AsyncMock()
    coordinator.async_send = partial(BrewCreatorDataUpdateCoordinator.async_send, coordinator)
    return coordinator


def sensor(coordinator: MagicMock, id: str, key: str, *, batch_info: bool = False) -> BrewCreatorSensor:
    """The sensor of the equipment with the description key, a batch info sensor if told so."""
    if batch_info:
        return BatchInfoSensor(coordinator, id, next(d for d in BATCH_INFO_SENSORS if d.key == key))
    equipment = coordinator.data.equipment[id]
    descriptions = FERMINATOR_SENSORS if isinstance(equipment, Ferminator) else HYDROMETER_SENSORS
    return BrewCreatorSensor(coordinator, id, next(d for d in descriptions if d.key == key))


def equipment(id: str, type: str, name: str | None = None, connected: list[str] = (), **extra) -> dict:
    """Equipment JSON as returned by BrewCreator's equipment list."""
    return {
        "id": id,
        "name": name or f"{type} {id}",
        "iotHubBrewEquipmentGroupId": type,
        "iotHubBrewEquipmentId": f"serial-{id}",
        "connectedEquipments": list(connected),
        **extra,
    }


def ferminator(
    id: str = "f",
    *,
    name: str = "Ferminator",
    connection_state: str = "Connected",
    is_active: bool | None = None,
    connected: list[str] = (),
    sw_version: str = "1.0",
    **extra,
) -> Ferminator:
    if is_active is None:
        is_active = connection_state == "Connected"
    return Ferminator(
        MagicMock(),
        equipment(
            id,
            "Ferminator",
            name,
            connected,
            **{
                "actualTemperature": 18.0,
                "setTemperature": 18.0,
                "brewName": None,
                "lastActivityTime": None,
                "isActive": is_active,
                "deviceTwinState": {
                    "connectionState": connection_state,
                    "reportedSwVersion": sw_version,
                    "reportedHwVersion": "A",
                },
                **extra,
            },
        ),
    )


def hydrometer(
    cls: type[Hydrometer] = Tilt,
    id: str = "t",
    *,
    sg: float | None = 1.014,
    temperature: float | None = 20,
    **extra,
) -> Hydrometer:
    return cls(
        MagicMock(),
        equipment(
            id,
            cls.__name__,
            **{
                "color": "TiltOrange",
                "sg": sg,
                "actualTemperature": temperature,
                "lastActivityTime": None,
                **extra,
            },
        ),
    )


def tilt(id: str = "t", **kwargs) -> Tilt:
    return hydrometer(Tilt, id, **kwargs)


def graviator(id: str = "g", **kwargs) -> Graviator:
    return hydrometer(Graviator, id, **kwargs)


def unsupported(id: str = "p", type: str = "Plaato") -> UnsupportedEquipment:
    return UnsupportedEquipment(MagicMock(), equipment(id, type, type))


def ha_device(identifier: str, *, id: str | None = None, disabled_by=None, **attrs) -> SimpleNamespace:
    """A device registry entry identified by (brewcreator, identifier)."""
    return SimpleNamespace(
        id=id or f"device-{identifier}",
        identifiers={("brewcreator", identifier)},
        disabled_by=disabled_by,
        name_by_user=None,
        **{
            key: attrs.get(key)
            for key in (
                "name",
                "manufacturer",
                "model",
                "serial_number",
                "sw_version",
                "hw_version",
            )
        },
    )


# A devicetwin frame as received from BrewCreator on 8 Nov 2026
DEVICETWIN_MESSAGE = {
    "type": 1,
    "target": "devicetwin",
    "arguments": [
        json.dumps(
            {
                "deviceId": "0723-si7-4yk",
                "opType": "telemetry",
                "LastActivityTime": "2026-11-08T17:41:11.6909257Z",
                "isTelemetry": False,
                "data": {
                    "Command": "dgrav",
                    "Value": "1.019",
                    "MessageId": 0,
                    "IotHubId": "0723-si7-4yk",
                    "Timestamp": "2026-11-08T17:41:11.6909703Z",
                    "Type": 1,
                },
                "data2": {
                    "Command": "abv",
                    "Value": "5.1",
                    "MessageId": 0,
                    "IotHubId": "0723-si7-4yk",
                    "Timestamp": "2026-11-08T17:41:11.6974602Z",
                    "Type": 1,
                },
            }
        )
    ],
}


def tool_parameters(handler: intent.IntentHandler) -> dict:
    """The OpenAPI parameters of the LLM tool for the intent, as an LLM agent gets them."""
    tool = llm.IntentTool(handler.intent_type, handler)
    return probatio.to_openapi(tool.parameters, custom_serializer=llm.selector_serializer)
