from abc import ABC
import asyncio
from asyncio import Task
import base64
from collections.abc import Awaitable, Callable, Mapping
import contextlib
from datetime import UTC, datetime, timedelta
from enum import Enum
import hashlib
import json as _json
import logging
import re
import secrets
from time import monotonic
from typing import Any, Protocol
import zoneinfo

import aiohttp
from aiohttp.client_exceptions import ClientConnectionResetError

_LOGGER = logging.getLogger(__name__)

# BrewCreator reports hydrometer activity times in Danish local time. Loaded once, as loading reads from disk.
COPENHAGEN_TZ = zoneinfo.ZoneInfo("Europe/Copenhagen")

# Equipment is listed in pages of this size, up to this many pages
EQUIPMENT_PAGE_SIZE = 100
EQUIPMENT_MAX_PAGES = 10

# A specific gravity outside this range is not a plausible reading, e.g. dgrav=0.000 from an empty Tilt slot
MIN_SPECIFIC_GRAVITY = 0.8
# Above any real wort, with margin for badly calibrated hydrometers
MAX_SPECIFIC_GRAVITY = 1.3

# A measured temperature (°C) outside this range is not plausible for wort or a fermentation chamber
MIN_TEMPERATURE = -20
MAX_TEMPERATURE = 120

# SignalR JSON protocol record separator, terminating every frame
SIGNALR_RECORD_SEPARATOR = "\x1e"

# A websocket connection closed by the server within this many seconds of connecting triggers a backoff
WEBSOCKET_SHORT_CONNECTION_SECONDS = 10
WEBSOCKET_MIN_BACKOFF_SECONDS = 2
WEBSOCKET_MAX_BACKOFF_SECONDS = 60


def _plausible_temperature(value: float | None) -> float | None:
    """The measured temperature, or None if it is missing or outside MIN_TEMPERATURE-MAX_TEMPERATURE."""
    if value is None or not MIN_TEMPERATURE <= value <= MAX_TEMPERATURE:
        return None
    return value


class FerminatorMode(Enum):
    READY = "Ready"
    COOLING = "Cooling"
    HEATING = "Heating"
    IDLE = "Idle"


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

    @property
    def key(self) -> str:
        """The lower case name, as the fermentation type sensor and set_batch_info action show it."""
        return self.name.lower()


class BatchInfo:
    """The batch info fields of equipment JSON.

    BrewCreator reports a field that isn't set as 0 or an empty string, which is returned as None.
    """

    def __init__(self, json: dict[str, any]) -> None:
        self._json = json

    def _value(self, key: str) -> Any:
        return self._json.get(key) or None

    @property
    def brew_name(self) -> str | None:
        return self._value("brewName")

    @property
    def brew_date(self) -> datetime | None:
        """The brew date, taken as UTC when it has no time zone."""
        str_date = self._json.get("brewDate")
        if str_date is None:
            return None
        brew_date = datetime.fromisoformat(str_date)
        return brew_date if brew_date.tzinfo is not None else brew_date.replace(tzinfo=UTC)

    @property
    def owner(self) -> str | None:
        return self._value("owner")

    @property
    def ebc(self) -> float | None:
        return self._value("ebc")

    @property
    def ibu(self) -> float | None:
        return self._value("ibu")

    @property
    def volume(self) -> float | None:
        return self._value("volume")

    @property
    def fermentation_type(self) -> FermentationType | None:
        """The fermentation type, or None if missing or not one BrewCreator is known to report."""
        try:
            return FermentationType(self._json.get("fermented"))
        except ValueError:
            return None

    @property
    def og(self) -> float | None:
        return self._value("og")

    @property
    def fg(self) -> float | None:
        return self._value("fg")

    @property
    def beer_style(self) -> str | None:
        return self._value("beerStyle")


class BrewCreatorError(Exception):
    pass


class BrewCreatorInvalidCredentialsError(BrewCreatorError):
    pass


class BrewCreatorAuthError(BrewCreatorError):
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
    def raw_type(self) -> str:
        """The equipment type as BrewCreator names it (iotHubBrewEquipmentGroupId), such as Ferminator."""
        return self._json["iotHubBrewEquipmentGroupId"]

    @property
    def actual_temperature(self) -> float | None:
        """Measured temperature, or None if missing or outside MIN_TEMPERATURE-MAX_TEMPERATURE."""
        return _plausible_temperature(self._json.get("actualTemperature"))

    @property
    def name(self) -> str:
        return self._json["name"]

    @property
    def last_activity_time(self) -> datetime | None:
        value = self._json.get("lastActivityTime")
        if value is None:
            return None
        return datetime.fromisoformat(value)

    @property
    def is_logging_data(self) -> bool:
        return self._json.get("isLoggingData")

    @property
    def batch_info(self) -> BatchInfo | None:
        if self._json.get("brewName"):
            return BatchInfo(self._json)
        return None

    @property
    def json(self) -> dict[str, any]:
        return self._json

    @property
    def is_connected(self) -> bool:
        """Whether the equipment is online and reporting."""
        return False

    @property
    def batch_owner(self) -> "BrewCreatorEquipment | None":
        """The equipment whose batch info applies to this equipment, if any.

        A Ferminator and an unlinked Graviator own their batch. A linked hydrometer gets its Ferminator's batch, which
        BrewCreator copies to it when linking. An unlinked Tilt has none, as it only reports through a Ferminator.
        """
        return None

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
        is_logging_data: bool | None = None,
    ) -> None:
        """Change the given fields, leaving those that are None as they are."""
        fields = {
            "brewName": brew_name,
            "owner": owner,
            "fg": fg,
            "og": og,
            "ebc": ebc,
            "ibu": ibu,
            "volume": volume,
            "fermented": fermentation_type.value if fermentation_type is not None else None,
            "beerStyle": beer_style,
            "isLoggingData": is_logging_data,
        }
        await self._update_equipment({k: v for k, v in fields.items() if v is not None})

    async def _update_equipment(self, json_payload: dict[str, any]) -> None:
        await self._api._update_equipment_state(self.id, json_payload)


class Hydrometer(BrewCreatorEquipment, ABC):
    def __init__(self, api: "BrewCreatorAPI", json: dict[str, any]) -> None:
        super().__init__(api, json)
        self._linked_ferminator: Ferminator | None = None

    @property
    def linked_ferminator(self) -> "Ferminator | None":
        """The Ferminator listing this hydrometer in its connectedEquipments, as set by resolve_links()."""
        return self._linked_ferminator

    @property
    def is_connected(self) -> bool:
        """Whether the hydrometer reports through a linked Ferminator that is connected, and has a reading."""
        ferminator = self.linked_ferminator
        if ferminator is None:
            return False
        has_data = self.specific_gravity is not None or self.actual_temperature is not None
        return has_data and ferminator.is_connected

    @property
    def batch_owner(self) -> BrewCreatorEquipment | None:
        return self.linked_ferminator

    @property
    def specific_gravity(self) -> float | None:
        """Specific gravity, or None if missing or outside MIN_SPECIFIC_GRAVITY-MAX_SPECIFIC_GRAVITY."""
        value = self._json.get("sg")
        if value is None or not MIN_SPECIFIC_GRAVITY <= value <= MAX_SPECIFIC_GRAVITY:
            return None
        return value

    @property
    def abv(self) -> float | None:
        return self._json.get("abv")

    @property
    def last_activity_time(self) -> datetime | None:
        """Time of the newest brew log point, or None if no brew has been logged.

        BrewCreator only updates it when a brew log point is written, not for every reading.
        """
        # The last activity time reported from the Tilt is not UTC. It's been observed as CEST during daylight saving time,
        # while ISO-8601 string claims it's UTC. Assume it's Danish local time and convert to UTC.
        # This has only been observed for Tilt devices (not Ferminator), and is assumed to also apply to Graviator.
        copenhagen_time = super().last_activity_time
        if copenhagen_time is None:
            return None
        return copenhagen_time.replace(tzinfo=COPENHAGEN_TZ).astimezone(UTC)


class Tilt(Hydrometer):
    @property
    def color(self) -> TiltColor:
        return TiltColor(self._json["color"])


class Graviator(Hydrometer):
    """Graviator hydrometer, reported like Tilt but without color.

    Unlike a Tilt, it can also report over Wi-Fi without a Ferminator, and then has a batch of its own.
    """

    @property
    def is_connected(self) -> bool:
        """Like any hydrometer while linked. Unlinked, it reports over Wi-Fi, and counts as connected while it has a
        valid reading."""
        if self.linked_ferminator is None:
            return self.specific_gravity is not None
        return super().is_connected

    @property
    def batch_owner(self) -> BrewCreatorEquipment:
        return self.linked_ferminator or self


class Ferminator(BrewCreatorEquipment):
    def __init__(self, api: "BrewCreatorAPI", json: dict[str, any]) -> None:
        super().__init__(api, json)
        self._linked_hydrometers: list[Hydrometer] = []
        self.inactive_too_long = False
        """Set by the coordinator once isActive has been false for longer than a cloud reconnect lasts."""

    @property
    def batch_owner(self) -> BrewCreatorEquipment:
        return self

    @property
    def actual_temperature(self) -> float | None:
        """Temperature from the linked hydrometer if it has a valid reading, else from the built-in probe."""
        hydrometer = self.active_hydrometer
        if hydrometer is not None:
            return hydrometer.actual_temperature
        return self.actual_temperature_builtin_probe

    @property
    def actual_temperature_builtin_probe(self) -> float | None:
        """Temperature from the Ferminator's built-in probe, whether or not a hydrometer is linked.

        None if missing or outside MIN_TEMPERATURE-MAX_TEMPERATURE.
        """
        return super().actual_temperature

    @property
    def fan_speed(self) -> int | None:
        return self._json.get("fanSpeed")

    @property
    def target_temperature(self) -> float | None:
        return self._json["setTemperature"]

    @property
    def mode(self) -> FerminatorMode | None:
        l_process = self._json.get("lProcess")
        if l_process is None:
            return None
        return FerminatorMode(l_process)

    @property
    def is_regulating(self) -> bool | None:
        """Whether temperature regulation is on, which it is in any mode but ready, or None if the mode is unknown.

        Follows lProcess, what the Ferminator reports, rather than isRegulatingTemperature (docs/brewcreator-api.md).
        """
        mode = self.mode
        return None if mode is None else mode != FerminatorMode.READY

    @property
    def _device_twin(self) -> dict[str, Any]:
        # Null for equipment that has never been online
        return self._json.get("deviceTwinState") or {}

    @property
    def sw_version(self) -> str | None:
        return self._device_twin.get("reportedSwVersion")

    @property
    def hw_version(self) -> str | None:
        return self._device_twin.get("reportedHwVersion")

    @property
    def is_active(self) -> bool:
        """Whether BrewCreator reports the Ferminator as active (isActive), which changes as soon as it notices a
        connect or disconnect."""
        return self._json.get("isActive") is True

    @property
    def is_connected(self) -> bool:
        """Whether the Ferminator is online.

        Online while it is active. isActive also drops for up to about 35 seconds every 48 minutes or so, while the
        Ferminator reconnects to the cloud without going offline, so an inactive Ferminator only counts as offline
        once the coordinator has seen it inactive for longer (inactive_too_long), or once the device twin's
        connectionState says Disconnected too. connectionState alone lags too much to rely on: by minutes when
        coming back, and when going offline by anything from seconds to several minutes.
        """
        if self.is_active:
            return True
        return self._device_twin.get("connectionState") == "Connected" and not self.inactive_too_long

    @property
    def connected_equipment_ids(self) -> list[str]:
        """IDs of the equipment this Ferminator lists as connected (connectedEquipments)."""
        return self._json.get("connectedEquipments") or []

    @property
    def linked_hydrometers(self) -> list[Hydrometer]:
        """Hydrometers linked to this Ferminator, as listed in its connectedEquipments."""
        return self._linked_hydrometers

    async def set_fan_speed(self, fan_speed: int) -> None:
        await self._update_equipment({"fanSpeed": fan_speed})

    async def set_target_temperature(self, temperature: float) -> None:
        await self._update_equipment({"setTemperature": temperature})

    async def set_regulating_temperature(self, is_regulating: bool) -> None:
        await self._update_equipment({"isRegulatingTemperature": is_regulating})

    async def link_hydrometer(self, hydrometer: Hydrometer | None) -> None:
        """Link a hydrometer to this Ferminator in place of the current one, or unlink it with None.

        BrewCreator moves the link on both sides and copies the Ferminator's batch info to the newly linked hydrometer.
        """
        await self._update_equipment(
            {"connectedEquipments": [hydrometer.id] if hydrometer is not None else []}
        )

    @property
    def active_hydrometer(self) -> Hydrometer | None:
        """The first linked hydrometer with a valid reading, which the Ferminator regulates on."""
        return next(
            (
                e
                for e in self.linked_hydrometers
                if e.specific_gravity is not None and e.actual_temperature is not None
            ),
            None,
        )


class UnsupportedEquipment(BrewCreatorEquipment):
    """Equipment of a type not supported by this integration."""


_EQUIPMENT_CLASSES: dict[str, type[BrewCreatorEquipment]] = {
    "Ferminator": Ferminator,
    "Tilt": Tilt,
    "Graviator": Graviator,
}
"""The equipment class of each supported iotHubBrewEquipmentGroupId."""

type EquipmentTypes = type[BrewCreatorEquipment] | tuple[type[BrewCreatorEquipment], ...]
"""One equipment type, or a tuple of them, as isinstance() takes."""

SUPPORTED_EQUIPMENT = (Ferminator, Hydrometer)
"""The equipment this integration has entities for."""

BATCH_INFO_EQUIPMENT = (Ferminator, Graviator)
"""The equipment that can have a batch of its own: a Ferminator, or a Graviator logging over Wi-Fi."""


def resolve_links(equipment: Mapping[str, BrewCreatorEquipment]) -> None:
    """Link each Ferminator and the hydrometers listed in its connectedEquipments, in both directions.

    The Ferminator's connectedEquipments is the single source of truth for links. BrewCreator also stores the link
    on the hydrometer (equipmentId), but it's unknown whether a Graviator sets it.
    """
    hydrometers = [e for e in equipment.values() if isinstance(e, Hydrometer)]
    for h in hydrometers:
        h._linked_ferminator = None
    for ferminator in equipment.values():
        if not isinstance(ferminator, Ferminator):
            continue
        connected_ids = set(ferminator.connected_equipment_ids)
        ferminator._linked_hydrometers = [h for h in hydrometers if h.id in connected_ids]
        for h in ferminator._linked_hydrometers:
            if h._linked_ferminator is None:
                h._linked_ferminator = ferminator


def linkable_hydrometers(
    ferminator: Ferminator, equipment: Mapping[str, BrewCreatorEquipment]
) -> list[Hydrometer]:
    """Return the hydrometers the Ferminator can link to, ordered by name: those not linked to another Ferminator."""
    return sorted(
        (
            e
            for e in equipment.values()
            if isinstance(e, Hydrometer) and e.linked_ferminator in (None, ferminator)
        ),
        key=lambda h: (h.name, h.id),
    )


WebsocketMessageCallback = Callable[[dict[str, Any] | None], Awaitable[None]]
"""Called with None after every successful websocket connect, and with each SignalR invocation message."""


async def _send_signalr(ws: aiohttp.ClientWebSocketResponse, message: str) -> None:
    """Send a SignalR JSON message, terminated by the record separator.

    BrewCreator waits for the separator, and cancels a handshake without one after 15 seconds.
    """
    await ws.send_str(message + SIGNALR_RECORD_SEPARATOR)


def parse_signalr_frames(data: str) -> list[dict[str, Any]]:
    """Split a websocket text message into SignalR JSON messages, skipping any that can't be parsed."""
    messages = []
    for record in data.split(SIGNALR_RECORD_SEPARATOR):
        if not record.strip():
            continue
        try:
            message = _json.loads(record)
        except ValueError:
            _LOGGER.debug("Ignoring unparsable SignalR record: %s", record)
            continue
        if isinstance(message, dict):
            messages.append(message)
        else:
            _LOGGER.debug("Ignoring unexpected SignalR record: %s", record)
    return messages


class TokenStorage(Protocol):
    async def load_tokens(self) -> tuple[str | None, str | None, datetime | None]: ...

    async def save_tokens(
        self,
        access_token: str | None,
        refresh_token: str | None,
        expire_time: datetime | None,
    ): ...


class BrewCreatorAPI:
    def __init__(
        self,
        username: str,
        password: str,
        token_storage: TokenStorage,
        session: aiohttp.ClientSession = None,
    ) -> None:
        self.__username = username
        self.__password = password
        self.__own_session = session is None
        self.__session = session or aiohttp.ClientSession()
        self.__message_callback: WebsocketMessageCallback | None = None
        self.__websocket_task: Task[Any] | None = None
        self.__token_storage = token_storage
        self.__access_token: str | None = None
        self.__refresh_token: str | None = None
        self.__expire_time: datetime | None = None
        self.__initial_token_load_completed: bool = False
        self.__logged_unsupported_types: set[str] = set()

    async def close(self):
        await self.stop_websocket()
        if self.__own_session:
            await self.__session.close()

    async def verify_username_and_password(self):
        await self.__set_tokens(await self.__exchange_username_and_password_for_tokens())

    async def list_equipment(self) -> dict[str, BrewCreatorEquipment]:
        """List all equipment on the account, fetching every page."""
        equipment_json = []
        for page in range(1, EQUIPMENT_MAX_PAGES + 1):
            data = await self.equipment_json(page)
            equipment_json.extend(e for e in data["data"] if e is not None)
            if not (data.get("metadata") or {}).get("hasNext"):
                break
        else:
            # A partial list would make devices look removed
            raise BrewCreatorError(
                f"More than {EQUIPMENT_MAX_PAGES} pages of equipment"
            )
        equipment = {e.id: e for e in map(self.__get_equipment_from_json, equipment_json)}
        resolve_links(equipment)
        return equipment

    async def equipment_json(self, page: int = 1) -> Any:
        data = await self.__do_authenticated_request(
            "GET",
            f"/api/v1.0/equipments?PageSize={EQUIPMENT_PAGE_SIZE}&PageNumber={page}&Logic=And&Filters=&Sorts=",
        )
        _LOGGER.debug("Equipment JSON: %s", data)
        return data

    async def start_websocket(self, message_callback: WebsocketMessageCallback) -> None:
        """Listen for telemetry in the background.

        The callback is called with None after every successful connect, so the caller can refetch
        state that changed while disconnected, and with each SignalR invocation message received.
        It should return quickly, as messages are not received while it runs.
        """
        self.__message_callback = message_callback
        if self.__websocket_task is not None and not self.__websocket_task.done():
            raise BrewCreatorError("WebSocket already running")
        self.__websocket_task = asyncio.create_task(self.__websocket_loop())

    async def stop_websocket(self) -> None:
        if self.__websocket_task:
            self.__websocket_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.__websocket_task
            self.__websocket_task = None

    async def _update_equipment_state(
        self, equipment_id: str, json_payload: dict[str, any]
    ) -> None:
        """Update equipment, raising BrewCreatorError if BrewCreator rejects the update."""
        json = await self.__do_authenticated_request(
            "PUT", f"/api/v1.0/equipments/{equipment_id}", json_payload
        )
        if not (json or {}).get("succeeded"):
            raise BrewCreatorError(f"BrewCreator rejected the update of {equipment_id}")

    def __get_equipment_from_json(
        self, equipment: dict[str, any]
    ) -> BrewCreatorEquipment:
        value = equipment["iotHubBrewEquipmentGroupId"]
        if (equipment_class := _EQUIPMENT_CLASSES.get(value)) is not None:
            return equipment_class(self, equipment)
        if value not in self.__logged_unsupported_types:
            self.__logged_unsupported_types.add(value)
            _LOGGER.warning(
                "Unsupported equipment type '%s' with ID '%s'", value, equipment["id"]
            )
        return UnsupportedEquipment(self, equipment)

    async def __websocket_loop(self) -> None:
        backoff_seconds = WEBSOCKET_MIN_BACKOFF_SECONDS
        failing = False
        while True:
            try:
                connected_seconds = await self.__websocket_connect_and_listen()
            except asyncio.CancelledError:
                _LOGGER.info(
                    "WebSocket listener stopped. Shutting down websocket task."
                )
                return
            except Exception as err:
                # Only the first failure in a row gets a traceback, e.g. while the network is down
                if failing:
                    _LOGGER.warning("WebSocket connection failed: %s", err)
                else:
                    _LOGGER.exception("Unexpected error in WebSocket listener")
                failing = True
                connected_seconds = None
            else:
                failing = False
            if (
                connected_seconds is not None
                and connected_seconds >= WEBSOCKET_SHORT_CONNECTION_SECONDS
            ):
                backoff_seconds = WEBSOCKET_MIN_BACKOFF_SECONDS
                continue
            # The server closed the connection straight away, the handshake failed, or connecting failed
            _LOGGER.debug("Reconnecting WebSocket in %d seconds", backoff_seconds)
            await asyncio.sleep(backoff_seconds)
            backoff_seconds = min(backoff_seconds * 2, WEBSOCKET_MAX_BACKOFF_SECONDS)

    async def __websocket_connect_and_listen(self) -> float | None:
        """Connect and receive messages until the connection ends.

        Returns how many seconds the connection lasted after a successful handshake, or None if the handshake failed.
        """
        response = await self.__do_authenticated_request(
            "POST", "/telemetry/negotiate?negotiateVersion=1"
        )
        connection_token = response["connectionToken"]
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
            await _send_signalr(ws, '{"protocol":"json","version":1}')
            handshake_response = await ws.receive()
            handshake_data = handshake_response.data
            if (
                not isinstance(handshake_data, str)
                or handshake_data.strip(SIGNALR_RECORD_SEPARATOR) != "{}"
            ):
                _LOGGER.warning(
                    "Unexpected handshake response: '%s'. Connection will likely be closed by server",
                    handshake_data,
                )
                connected_at = None
            else:
                await _send_signalr(
                    ws, '{"arguments":["devicetwin"],"target":"SubscribeToUser","type":1}'
                )
                _LOGGER.info("Successfully connected to %s", wss_host)
                connected_at = monotonic()
                # Refetch state, as anything may have changed while disconnected
                await self.__message_callback(None)
            # Send text message every 10th second to keep connection alive in a separate task
            ping_task = asyncio.create_task(self.__websocket_signalr_ping(ws))
            try:
                async for msg in ws:  # type: aiohttp.WSMessage
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        await self.__handle_websocket_text(msg.data)
                    elif msg.type == aiohttp.WSMsgType.ERROR:
                        _LOGGER.error(
                            "WebSocket failed with error: %s",
                            ws.exception(),
                        )
                        break
                    else:
                        _LOGGER.error("Unexpected WebSocket message type: %s", msg.type)
            finally:
                ping_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await ping_task
            # The iteration ends when the server closes the connection
            _LOGGER.info("WebSocket connection closed")
            if connected_at is None:
                return None
            return monotonic() - connected_at

    async def __handle_websocket_text(self, data: str) -> None:
        for message in parse_signalr_frames(data):
            message_type = message.get("type")
            if message_type == 6:
                _LOGGER.debug("Received WebSocket SignalR ping")
            elif message_type == 1:
                _LOGGER.debug(
                    "Received a message that will trigger a state update: %s",
                    message,
                )
                await self.__message_callback(message)
            elif message_type == 7:
                _LOGGER.debug("Received WebSocket SignalR close: %s", message)
            else:
                _LOGGER.debug("Received unexpected message: %s", message)

    async def __websocket_signalr_ping(self, ws: aiohttp.ClientWebSocketResponse):
        while True:
            await asyncio.sleep(10)
            try:
                _LOGGER.debug("Sending WebSocket SignalR ping message")
                await _send_signalr(ws, '{"type":6}')
            except (
                asyncio.CancelledError,
                ClientConnectionResetError,
            ):
                _LOGGER.debug("WebSocket SignalR ping task stopped")
                return
            except Exception:
                _LOGGER.exception("Failed to send WebSocket SignalR ping")
                return

    async def __do_authenticated_request(
        self, method: str, path: str, json: dict[str, any] | None = None
    ) -> dict[str, any] | None:
        max_attempts = 5
        sleep_seconds_between_attempts = 1
        for attempt in range(max_attempts):
            try:
                await self.__update_access_token_if_invalid()
                _LOGGER.debug("Performing request %s %s", method, path)
                async with self.__session.request(
                    method,
                    f"https://api.brewcreator.com{path}",
                    headers={
                        "Authorization": f"Bearer {self.__access_token}",
                        "Accept": "application/json",
                    },
                    json=json,
                ) as response:
                    if response.status == 401:
                        raise BrewCreatorAuthError(  # noqa: TRY301
                            f"Failed to {method} {path}: {response.status}"
                        )
                    if response.status == 500:
                        _LOGGER.info(
                            "Failed to %s %s: %s", method, path, response.status
                        )
                        continue
                    if response.status != 200:
                        raise BrewCreatorError(
                            f"Failed to {method} {path}: {response.status}"
                        )
                    return await response.json() if response.content else None
            except BrewCreatorAuthError as e:
                _LOGGER.info(
                    "Failed to authenticate. Attempt %d of %d. Retrying in %d seconds: %s",
                    attempt + 1,
                    max_attempts,
                    sleep_seconds_between_attempts,
                    e,
                )
                self.__access_token = None
                self.__refresh_token = None
                self.__expire_time = None
                await self.__token_storage.save_tokens(None, None, None)
            except (aiohttp.ClientError, TimeoutError) as e:
                # Wrapped so callers only need to handle BrewCreatorError. Not repr(e): for a ClientResponseError
                # that includes the request headers, with the access token.
                raise BrewCreatorError(f"Failed to {method} {path}: {type(e).__name__}: {e}") from e
            await asyncio.sleep(sleep_seconds_between_attempts)
        raise BrewCreatorError(f"Failed to {method} {path} after {max_attempts} attempts")

    async def __update_access_token_if_invalid(self):
        if not self.__initial_token_load_completed:
            (
                self.__access_token,
                self.__refresh_token,
                self.__expire_time,
            ) = await self.__token_storage.load_tokens()
            self.__initial_token_load_completed = True
            _LOGGER.debug(
                "Loaded access token from storage with expiry %s", self.__expire_time
            )
        if not self.__is_access_token_missing_or_expired():
            _LOGGER.debug(
                "Access token is assumed valid as it has expiry %s",
                self.__expire_time,
            )
            return
        if self.__refresh_token is not None:
            await self.__set_tokens(await self.__exchange_refresh_token_for_tokens())
            return
        await self.__set_tokens(
            await self.__exchange_username_and_password_for_tokens()
        )

    def __is_access_token_missing_or_expired(self) -> bool:
        if self.__access_token is None:
            _LOGGER.debug("Access token is missing")
            return True
        if self.__expire_time is None:
            _LOGGER.debug("Expire time is missing")
            return True
        if datetime.now() > self.__expire_time - timedelta(minutes=2):
            _LOGGER.debug("Access token is expired (expiry=%s)", self.__expire_time)
            return True
        return False

    async def __request_tokens(
        self, grant: dict[str, str], what: str, **request_kwargs: Any
    ) -> tuple[str, str, datetime]:
        """Request new tokens from the token endpoint with the grant's form fields."""
        async with self.__session.post(
            "https://identity.brewcreator.com/connect/token",
            data={"client_id": "brew-creator", **grant},
            **request_kwargs,
        ) as response:
            if response.status != 200:
                raise BrewCreatorAuthError(f"Failed to {what}: {response.status}")
            json = await response.json()
            return (
                json["access_token"],
                json["refresh_token"],
                datetime.now() + timedelta(seconds=json["expires_in"]),
            )

    async def __exchange_refresh_token_for_tokens(self) -> tuple[str, str, datetime]:
        _LOGGER.debug("Exchanging refresh token for new tokens")
        return await self.__request_tokens(
            {"grant_type": "refresh_token", "refresh_token": self.__refresh_token},
            "refresh tokens",
        )

    async def __exchange_username_and_password_for_tokens(
        self,
    ) -> tuple[str, str, datetime]:
        csrf_token = await self.__get_csrf_token()
        code, code_verifier = await self.__exchange_username_and_password_for_code(csrf_token)
        _LOGGER.debug("Exchanging code for tokens")
        return await self.__request_tokens(
            {
                "grant_type": "authorization_code",
                "code_verifier": code_verifier,
                "code": code,
                "redirect_uri": "https://brewcreator.com",
            },
            "exchange code for tokens",
            timeout=20,
        )

    async def __get_csrf_token(self) -> str:
        async with self.__session.get(
            "https://identity.brewcreator.com/Account/Login", timeout=60
        ) as response:
            if response.status != 200:
                raise BrewCreatorError(f"Failed to get CSRF token: {response.status}")
            pattern = (
                '<input name="__RequestVerificationToken" type="hidden" value="([^"]*)"'
            )
            csrf_token = re.search(pattern, await response.text()).group(1)
            _LOGGER.debug("Found CSRF token of length %d", len(csrf_token))
            return csrf_token

    async def __exchange_username_and_password_for_code(self, csrf_token: str) -> tuple[str, str]:
        _LOGGER.debug("Exchanging username and password for code")
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
                "Email": self.__username,
                "Password": self.__password,
                "__RequestVerificationToken": csrf_token,
            },
        ) as response:
            if response.status != 200:
                raise BrewCreatorAuthError(f"Failed to authenticate: {response.status}")
            if response.url.path == "/account/login":
                raise BrewCreatorInvalidCredentialsError("Invalid username/password")
            if "code" not in response.url.query:
                raise BrewCreatorAuthError(f"Cannot find 'code' in {response.url.path_qs}")
            return (
                response.url.query["code"],
                code_verifier,
            )

    async def __set_tokens(self, tokens: tuple[str, str, datetime]) -> None:
        _LOGGER.debug("Storing new access token with expiry %s", tokens[2])
        self.__access_token, self.__refresh_token, self.__expire_time = tokens
        await self.__token_storage.save_tokens(
            self.__access_token, self.__refresh_token, self.__expire_time
        )
