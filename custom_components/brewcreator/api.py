from abc import ABC
import asyncio
from asyncio import Task
import base64
from collections.abc import Awaitable, Callable
import contextlib
from datetime import datetime
from enum import Enum
import hashlib
import logging
import re
import secrets
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)


class FerminatorMode(Enum):
    READY = "Ready"
    COOLING = "Cooling"
    HEATING = "Heating"
    IDLE = "Idle"


class FerminatorStatus(Enum):
    START = "Start"
    STOP = "Stop"


class EquipmentType(Enum):
    TILT = "Tilt"
    FERMINATOR = "Ferminator"
    UNKNOWN = "Unknown"


class TiltColor(Enum):
    ORANGE = "TiltOrange"
    YELLOW = "TiltYellow"
    GREEN = "TiltGreen"
    BLUE = "TiltBlue"
    PURPLE = "TiltPurple"
    RED = "TiltRed"
    PINK = "TiltPink"
    BLACK = "TiltBlack"


class FermentationType(Enum):
    TOP = "Top"
    BOTTOM = "Bottom"


class BatchInfo:
    def __init__(self, json: dict[str, any]) -> None:
        self._json = json

    @property
    def brew_name(self) -> str:
        return self._json.get("brewName")

    @property
    def brew_date(self) -> datetime | None:
        str_date = self._json.get("brewDate")
        if str_date is None:
            return None
        return datetime.fromisoformat(str_date)

    @property
    def owner(self) -> str:
        return self._json.get("owner")

    @property
    def ebc(self) -> float:
        return self._json.get("ebc")

    @property
    def ibu(self) -> float:
        return self._json.get("ibu")

    @property
    def volume(self) -> float:
        return self._json.get("volume")

    @property
    def fermentation_type(self) -> FermentationType:
        return FermentationType(self._json.get("fermented"))

    @property
    def og(self) -> float:
        return self._json.get("og")

    @property
    def fg(self) -> float:
        return self._json.get("fg")

    @property
    def beer_style(self) -> str:
        return self._json.get("beerStyle")


class BrewCreatorError(Exception):
    pass


class BrewCreatorInvalidAuthError(BrewCreatorError):
    pass


class BrewCreatorEquipment(ABC):
    def __init__(self, api: "BrewCreatorAPI", json: dict[str, any]) -> None:
        self._api = api
        self._json = json

    @property
    def id(self) -> str:
        return self._json["id"]

    @property
    def serial_number(self) -> str:
        return self._json["iotHubBrewEquipmentId"]

    @property
    def equipment_type(self) -> EquipmentType:
        value = self._json["iotHubBrewEquipmentGroupId"]
        try:
            return EquipmentType(value)
        except ValueError:
            _LOGGER.warning("Unknown equipment type '%s' with ID '%s'", value, self.id)
            return EquipmentType.UNKNOWN

    @property
    def actual_temperature(self) -> float | None:
        return self._json["actualTemperature"]

    @property
    def name(self) -> str:
        return self._json["name"]

    @property
    def last_activity_time(self) -> datetime:
        return datetime.fromisoformat(self._json["lastActivityTime"])

    @property
    def is_logging_data(self) -> bool:
        return self._json.get("isLoggingData")

    @property
    def batch_info(self) -> BatchInfo | None:
        if self._json["brewName"]:
            return BatchInfo(self._json)
        return None

    @property
    def json(self) -> dict[str, any]:
        return self._json

    async def set_batch_info(
        self,
        brew_name: str | None = None,
        owner: str | None = None,
        fg: float | None = None,
        og: float | None = None,
        ebc: float | None = None,
        ibu: float | None = None,
        volume: float | None = None,
        fermentation_type: FermentationType | None = None,
        beer_style: str | None = None,
    ) -> bool:
        options = {}
        if brew_name is not None:
            options["brewName"] = brew_name
        if owner is not None:
            options["owner"] = owner
        if fg is not None:
            options["fg"] = fg
        if og is not None:
            options["og"] = og
        if ebc is not None:
            options["ebc"] = ebc
        if ibu is not None:
            options["ibu"] = ibu
        if volume is not None:
            options["volume"] = volume
        if fermentation_type is not None:
            options["fermented"] = fermentation_type.value
        if beer_style is not None:
            options["beerStyle"] = beer_style
        return await self._update_equipment(options)

    async def _update_equipment(self, json_payload: dict[str, any]) -> bool:
        return await self._api._update_equipment_state(self.id, json_payload)


class Tilt(BrewCreatorEquipment):
    def __init__(self, api: "BrewCreatorAPI", json: dict[str, any]) -> None:
        super().__init__(api, json)

    @property
    def specific_gravity(self) -> float | None:
        return self._json["sg"]

    @property
    def color(self) -> TiltColor:
        return TiltColor(self._json["color"])

    @property
    def abv(self) -> float:
        return self._json["abv"]


class Ferminator(BrewCreatorEquipment):
    def __init__(self, api: "BrewCreatorAPI", json: dict[str, any]) -> None:
        super().__init__(api, json)
        self._connected_equipment_list: list[BrewCreatorEquipment] = []

    @property
    def actual_temperature(self) -> float | None:
        tilt = self.__connected_tilt
        if tilt is not None:
            return tilt.actual_temperature
        return self._json["actualTemperature"]

    @property
    def actual_temperature_builtin_probe(self):
        return self._json["actualTemperature"]

    @property
    def fan_speed(self) -> int | None:
        return self._json["fanSpeed"]

    @property
    def target_temperature(self) -> float | None:
        return self._json["setTemperature"]

    @property
    def mode(self) -> FerminatorMode | None:
        l_process = self._json["lProcess"]
        if l_process is None:
            return None
        return FerminatorMode(l_process)

    @property
    def status(self) -> FerminatorStatus | None:
        l_status = self._json["lStatus"]
        if l_status is None:
            return None
        return FerminatorStatus(l_status)

    @property
    def sw_version(self) -> str:
        return self._json["deviceTwinState"]["reportedSwVersion"]

    @property
    def hw_version(self) -> str:
        return self._json["deviceTwinState"]["reportedHwVersion"]

    @property
    def is_connected(self) -> bool:
        return self._json["deviceTwinState"]["connectionState"] == "Connected"

    @property
    def connected_equipment(self) -> list[BrewCreatorEquipment]:
        return self._connected_equipment_list

    async def set_fan_speed(self, fan_speed: int) -> bool:
        return await self._update_equipment({"fanSpeed": fan_speed})

    async def set_target_temperature(self, temperature: float) -> bool:
        return await self._update_equipment({"setTemperature": temperature})

    async def set_regulating_temperature(self, is_regulating: bool) -> bool:
        return await self._update_equipment({"isRegulatingTemperature": is_regulating})

    async def set_logging_data(self, is_logging_data: bool) -> bool:
        return await self._update_equipment({"isLoggingData": is_logging_data})

    def _update_connected_equipment(self, equipment: list[BrewCreatorEquipment]):
        self._connected_equipment_list = [
            e for e in equipment if e.id in self._json["connectedEquipments"]
        ]

    @property
    def __connected_tilt(self) -> Tilt | None:
        return next(
            (
                e
                for e in self.connected_equipment
                if isinstance(e, Tilt) and e.is_logging_data
            ),
            None,
        )


class BrewCreatorAPI:
    def __init__(
        self,
        username: str,
        password: str,
        session: aiohttp.ClientSession = None,
    ) -> None:
        self.__username = username
        self.__password = password
        self.__session = session or aiohttp.ClientSession()
        self.__access_token: str | None = None
        self.__update_callback: (
            Callable[[dict[str, BrewCreatorEquipment]], Awaitable[None]] | None
        ) = None
        self.__websocket_task: Task[Any] | None = None
        self.__websocket_ping_task: Task[Any] | None = None

    async def close(self):
        await self.stop_websocket()
        await self.__session.close()

    async def reauthenticate(self):
        csrf_token = await self.__get_csrf_token()
        code, code_verifier = await self.__get_code(
            csrf_token, self.__username, self.__password
        )
        self.__access_token = await self.__get_tokens(code, code_verifier)

    async def list_equipment(self) -> dict[str, BrewCreatorEquipment]:
        data = await self.equipment_json()
        equipment_list = [
            self.__create_equipment(e) for e in data["data"] if e is not None
        ]
        for e in filter(lambda x: isinstance(x, Ferminator), equipment_list):
            e._update_connected_equipment(equipment_list)
        return {e.id: e for e in equipment_list}

    async def equipment_json(self) -> Any:
        await self.reauthenticate()
        async with self.__session.get(
            "https://api.brewcreator.com/api/v1.0/equipments?PageSize=100&PageNumber=1&Logic=And&Filters=&Sorts=",
            headers=self.__get_headers(),
        ) as response:
            data = await response.json()
            _LOGGER.debug("Equipment JSON: %s", data)
            return data

    async def start_websocket(
        self,
        update_callback: Callable[[dict[str, BrewCreatorEquipment]], Awaitable[None]],
    ) -> None:
        self.__update_callback = update_callback
        if (
            self.__websocket_task is None
            or self.__websocket_task.done()
            or self.__websocket_task.cancelled()
        ):
            self.__websocket_task = asyncio.create_task(self.__websocket_loop())
        else:
            raise BrewCreatorError("WebSocket already running")

    async def stop_websocket(self) -> None:
        if self.__websocket_task:
            self.__websocket_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.__websocket_task
            self.__websocket_task = None

    async def _update_equipment_state(
        self, equipment_id: str, json_payload: dict[str, any]
    ) -> bool:
        await self.reauthenticate()
        async with self.__session.put(
            f"https://api.brewcreator.com/api/v1.0/equipments/{equipment_id}",
            json=json_payload,
            headers=self.__get_headers(),
        ) as response:
            return (await response.json())["succeeded"]

    def __create_equipment(
        self, equipment: dict[str, any]
    ) -> BrewCreatorEquipment | None:
        equipment_type = EquipmentType(equipment["iotHubBrewEquipmentGroupId"])
        if equipment_type == EquipmentType.FERMINATOR:
            return Ferminator(self, equipment)
        if equipment_type == EquipmentType.TILT:
            return Tilt(self, equipment)
        _LOGGER.warning(
            "Unknown equipment type '%s' with ID '%s'",
            equipment_type,
            equipment["id"],
        )
        return None

    def __get_headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.__access_token}",
            "Accept": "application/json",
        }

    async def __websocket_loop(self) -> None:
        while True:
            try:
                await self.__websocket_connect_and_listen()
            except asyncio.CancelledError:
                _LOGGER.info(
                    "WebSocket listener stopped. Shutting down websocket task."
                )
                return
            except Exception as e:
                _LOGGER.exception("Unexpected error in WebSocket listener: %s", e)
                await asyncio.sleep(60)

    async def __websocket_connect_and_listen(self):
        await self.reauthenticate()
        async with self.__session.post(
            "https://api.brewcreator.com/telemetry/negotiate?negotiateVersion=1",
            headers=self.__get_headers(),
        ) as response:
            connection_token = (await response.json())["connectionToken"]
        wss_host = "wss://api.brewcreator.com"
        url = f"{wss_host}/telemetry?id={connection_token}&access_token={self.__access_token}"
        async with self.__session.ws_connect(
            url,
            autoclose=True,
            autoping=True,
            heartbeat=10,
            timeout=30,
            receive_timeout=None,
        ) as ws:
            await ws.send_str('{"protocol":"json","version":1}')
            handshake_response = await ws.receive()
            if handshake_response.data != "{}":
                _LOGGER.warning(
                    "Unexpected handshake response: '%s'. Connection will likely be closed by server",
                    handshake_response.data,
                )
            else:
                await ws.send_str(
                    '{"arguments":["devicetwin"],"target":"SubscribeToUser","type":1}'
                )
                _LOGGER.info("Successfully connected to %s", wss_host)
            # Send text message every 10th second to keep connection alive in a separate task
            if self.__websocket_ping_task is not None:
                self.__websocket_ping_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self.__websocket_ping_task
                self.__websocket_ping_task = None
            self.__websocket_ping_task = asyncio.create_task(
                self.__websocket_signalr_ping(ws)
            )
            async for msg in ws:  # type: aiohttp.WSMessage
                if msg.type == aiohttp.WSMsgType.TEXT:
                    if msg.data == '{"type":6}':
                        _LOGGER.debug("Received WebSocket SignalR ping")
                    elif msg.data.startswith('{"type":1'):
                        _LOGGER.debug(
                            "Received a message that will trigger a state update: %s",
                            msg.data,
                        )
                        await self.__update_callback(await self.list_equipment())
                    else:
                        _LOGGER.debug("Received unexpected message: %s", msg.data)
                elif msg.type == aiohttp.WSMsgType.CLOSED:
                    _LOGGER.info("WebSocket connection closed")
                    await asyncio.sleep(60)
                    return
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    _LOGGER.error(
                        "WebSocket failed with error: %s",
                        ws.exception(),
                    )
                    await asyncio.sleep(60)
                    return
                else:
                    _LOGGER.error("Unexpected WebSocket message type: %s", msg.type)

    async def __websocket_signalr_ping(self, ws: aiohttp.ClientWebSocketResponse):
        while True:
            await asyncio.sleep(10)
            try:
                _LOGGER.debug("Sending WebSocket SignalR ping message")
                await ws.send_str('{"type":6}')
            except asyncio.CancelledError:
                _LOGGER.debug("WebSocket SignalR ping task stopped")
                return
            except Exception as e:
                _LOGGER.exception("Failed to send WebSocket SignalR ping: %s", e)
                return

    async def __get_csrf_token(self) -> str:
        async with self.__session.get(
            "https://identity.brewcreator.com/Account/Login", timeout=60
        ) as response:
            if response.status != 200:
                raise BrewCreatorError(f"Failed to get CSRF token: {response.status}")
            pattern = (
                '<input name="__RequestVerificationToken" type="hidden" value="([^"]*)"'
            )
            return re.search(pattern, await response.text()).group(1)

    async def __get_code(
        self, csrf_token: str, username: str, password: str
    ) -> tuple[str, str]:
        nonce = secrets.token_urlsafe(16)
        state = secrets.token_urlsafe(16)
        code_verifier = secrets.token_urlsafe(67)
        sha256 = hashlib.sha256()
        sha256.update(code_verifier.encode())
        code_challenge = base64.urlsafe_b64encode(sha256.digest()).decode().rstrip("=")
        async with self.__session.post(
            (
                f"https://identity.brewcreator.com/account/login?returnurl=%2Fconnect%2Fauthorize%3Fclient_id%3Dbrew-creator%26redirect_uri%"
                f"3Dhttps%253A%252F%252Fbrewcreator.com%26response_type%3Dcode%26scope%3Dopenid%2520profile%2520email%2520phone%2520roles%2520brewer-access"
                f"%2520offline_access%26nonce%3D{nonce}%26state%3D{state}%26code_challenge%3D{code_challenge}%26code_challenge_method%3DS256%26ui_locales%3Den-US"
            ),
            data={
                "Email": username,
                "Password": password,
                "__RequestVerificationToken": csrf_token,
            },
        ) as response:
            if response.status != 200:
                if response.status == 400:
                    # TODO Automatic retry on 400
                    _LOGGER.info("Response headers from 400: %s", response.headers)
                raise BrewCreatorInvalidAuthError(
                    f"Failed to authenticate: {response.status}"
                )
            code = response.url.query["code"]
            if not code:
                raise BrewCreatorInvalidAuthError(
                    f"Failed to get authorization code: {response.url.raw_query_string}"
                )
            return (
                code,
                code_verifier,
            )

    async def __get_tokens(self, code: str, code_verifier: str) -> str:
        async with self.__session.post(
            "https://identity.brewcreator.com/connect/token",
            data={
                "grant_type": "authorization_code",
                "client_id": "brew-creator",
                "code_verifier": code_verifier,
                "code": code,
                "redirect_uri": "https://brewcreator.com",
            },
        ) as response:
            json = await response.json()
            return json["access_token"]
