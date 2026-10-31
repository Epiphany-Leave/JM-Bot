"""Small forward-WebSocket OneBot v11 client."""

import asyncio
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Any

import websockets

logger = logging.getLogger(__name__)


class OneBotError(Exception):
    pass


class OneBotDisconnected(OneBotError):
    pass


class OneBotAPIError(OneBotError):
    pass


class OneBotClient:
    def __init__(
        self,
        url: str,
        token: str,
        on_event: Callable[[dict, int], Awaitable[None]],
        api_timeout: float = 180,
    ):
        self.url = url
        self.token = token
        self.on_event = on_event
        self.api_timeout = api_timeout
        self.generation = 0
        self._socket = None
        self._runner = None
        self._stopping = asyncio.Event()
        self._pending: dict[str, asyncio.Future] = {}
        self._event_tasks: set[asyncio.Task] = set()

    @property
    def connected(self) -> bool:
        return self._socket is not None

    async def run(self) -> None:
        if self._runner is not None:
            raise RuntimeError("OneBot client is already running")
        self._runner = asyncio.current_task()
        delay = 1
        try:
            while not self._stopping.is_set():
                try:
                    headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
                    async with websockets.connect(
                        self.url,
                        additional_headers=headers,
                        proxy=None,
                        open_timeout=10,
                        max_size=4 * 1024 * 1024,
                    ) as socket:
                        self._socket = socket
                        self.generation += 1
                        delay = 1
                        logger.info("OneBot connected (generation %s)", self.generation)
                        async for raw in socket:
                            self._receive(raw)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    # Connection exception text may contain the authenticated URL.
                    logger.warning("OneBot connection lost (%s)", type(exc).__name__)
                finally:
                    self._socket = None
                    self._fail_pending()
                if not self._stopping.is_set():
                    try:
                        await asyncio.wait_for(self._stopping.wait(), timeout=delay)
                    except TimeoutError:
                        pass
                    delay = min(delay * 2, 30)
        except asyncio.CancelledError:
            if not self._stopping.is_set():
                raise
        finally:
            self._runner = None

    def _receive(self, raw: str | bytes) -> None:
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            logger.warning("Ignoring invalid OneBot JSON")
            return
        if not isinstance(payload, dict):
            logger.warning("Ignoring non-object OneBot JSON")
            return
        if "echo" in payload:
            echo = payload["echo"]
            future = self._pending.get(echo) if isinstance(echo, str) else None
            if future is not None and not future.done():
                future.set_result(payload)
            return
        task = asyncio.create_task(self.on_event(payload, self.generation))
        self._event_tasks.add(task)
        task.add_done_callback(self._event_done)

    def _event_done(self, task: asyncio.Task) -> None:
        self._event_tasks.discard(task)
        if not task.cancelled():
            error = task.exception()
            if error is not None:
                logger.error("OneBot event handler failed (%s)", type(error).__name__)

    def _fail_pending(self) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(OneBotDisconnected("OneBot connection closed"))
        self._pending.clear()

    async def call(self, action: str, **params: Any) -> Any:
        socket = self._socket
        generation = self.generation
        if socket is None or self._stopping.is_set():
            raise OneBotDisconnected("OneBot is not connected")
        echo = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self._pending[echo] = future
        try:
            async with asyncio.timeout(self.api_timeout):
                await socket.send(json.dumps({"action": action, "params": params, "echo": echo}))
                response = await future
            if generation != self.generation:
                raise OneBotDisconnected("OneBot connection changed during API call")
            if response.get("status") != "ok" or type(response.get("retcode")) is not int or response["retcode"] != 0:
                raise OneBotAPIError(f"OneBot API {action} failed (retcode={response.get('retcode')})")
            return response.get("data")
        except TimeoutError as exc:
            raise OneBotError(f"OneBot API {action} timed out") from exc
        except websockets.ConnectionClosed as exc:
            raise OneBotDisconnected("OneBot connection closed during API call") from exc
        finally:
            self._pending.pop(echo, None)
            if not future.done():
                future.cancel()
            elif not future.cancelled():
                future.exception()

    async def close(self) -> None:
        self._stopping.set()
        self._fail_pending()
        if self._socket is not None:
            await self._socket.close()
        runner = self._runner
        if runner is not None and runner is not asyncio.current_task():
            runner.cancel()
            with suppress(asyncio.CancelledError):
                await runner
