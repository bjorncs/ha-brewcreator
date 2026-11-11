import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp

from custom_components.brewcreator.api import BrewCreatorAPI


class StopLoop(BaseException):
    """Ends the websocket loop, which only catches Exception."""


class FakeWebSocket:
    """A websocket that answers the handshake, yields the given text frames, then is closed by the server."""

    def __init__(self, frames: list[str] = (), handshake: str = "{}\x1e"):
        self.frames = list(frames)
        self.handshake = handshake
        self.sent: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def send_str(self, data: str) -> None:
        self.sent.append(data)

    async def receive(self):
        return SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data=self.handshake)

    def exception(self):
        return None

    async def __aiter__(self):
        for frame in self.frames:
            yield SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data=frame)


class WebSocketLoopTestCase(unittest.IsolatedAsyncioTestCase):
    async def run_loop(self, connections: list[FakeWebSocket], durations: list[float]) -> tuple[list, list[float]]:
        """Run the websocket loop over the given connections, each lasting the given seconds."""
        session = MagicMock()
        session.ws_connect.side_effect = [*connections, StopLoop()]
        api = BrewCreatorAPI("user", "pass", MagicMock(), session)
        api._BrewCreatorAPI__access_token = "token"
        callback_args = []

        async def callback(message):
            callback_args.append(message)

        api._BrewCreatorAPI__message_callback = callback
        sleeps = []

        async def fake_sleep(seconds):
            sleeps.append(seconds)

        clock = []
        for duration in durations:
            clock += [0.0, duration]
        with (
            patch.object(
                api,
                "_BrewCreatorAPI__do_authenticated_request",
                AsyncMock(return_value={"connectionToken": "c"}),
            ),
            patch.object(api, "_BrewCreatorAPI__websocket_signalr_ping", AsyncMock()),
            patch("custom_components.brewcreator.api.asyncio.sleep", fake_sleep),
            patch("custom_components.brewcreator.api.monotonic", side_effect=clock),
            self.assertRaises(StopLoop),
        ):
            await api._BrewCreatorAPI__websocket_loop()
        return callback_args, sleeps

    async def test_refreshes_after_every_connect(self):
        callback_args, _ = await self.run_loop([FakeWebSocket(), FakeWebSocket()], durations=[120, 120])
        self.assertEqual([None, None], callback_args)

    async def test_backs_off_when_server_closes_repeatedly(self):
        _, sleeps = await self.run_loop([FakeWebSocket() for _ in range(6)], durations=[0, 1, 0, 0, 0, 0])
        self.assertEqual([2, 4, 8, 16, 32, 60], sleeps)

    async def test_backoff_resets_after_a_long_connection(self):
        _, sleeps = await self.run_loop([FakeWebSocket(), FakeWebSocket(), FakeWebSocket()], durations=[0, 300, 0])
        self.assertEqual([2, 2], sleeps)

    async def test_backs_off_after_connection_error(self):
        callback_args, sleeps = await self.run_loop(
            [OSError("DNS timeout"), OSError("DNS timeout"), FakeWebSocket()],
            durations=[120],
        )
        self.assertEqual([2, 4], sleeps)
        self.assertEqual([None], callback_args)

    async def test_only_first_error_in_a_row_logs_a_traceback(self):
        with self.assertLogs("custom_components.brewcreator.api", "WARNING") as logs:
            await self.run_loop(
                [OSError("DNS timeout"), OSError("DNS timeout"), OSError("DNS timeout")],
                durations=[],
            )
        failures = [r for r in logs.records if "DNS timeout" in r.getMessage() or r.exc_info]
        self.assertEqual(["ERROR", "WARNING", "WARNING"], [r.levelname for r in failures])
        self.assertIsNotNone(failures[0].exc_info)
        self.assertIsNone(failures[1].exc_info)

    async def test_backs_off_after_failed_handshake(self):
        callback_args, sleeps = await self.run_loop([FakeWebSocket(handshake='{"error":"x"}\x1e')], durations=[])
        self.assertEqual([], callback_args)
        self.assertEqual([2], sleeps)

    async def test_passes_on_invocation_messages_from_split_frames(self):
        invocation = '{"type":1,"target":"devicetwin","arguments":["{}"]}'
        callback_args, _ = await self.run_loop(
            [FakeWebSocket([f'{{"type":6}}\x1e{invocation}\x1e{{"type":7,"allowReconnect":true}}\x1e'])],
            durations=[120],
        )
        self.assertEqual(
            [None, {"type": 1, "target": "devicetwin", "arguments": ["{}"]}],
            callback_args,
        )

    async def test_subscribes_after_handshake(self):
        ws = FakeWebSocket()
        await self.run_loop([ws], durations=[120])
        self.assertEqual(
            # Every message ends with the record separator, or BrewCreator cancels the handshake
            [
                '{"protocol":"json","version":1}\x1e',
                '{"arguments":["devicetwin"],"target":"SubscribeToUser","type":1}\x1e',
            ],
            ws.sent,
        )


if __name__ == "__main__":
    unittest.main()
