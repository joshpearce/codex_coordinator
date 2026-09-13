from __future__ import annotations

import asyncio
import json
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any


class ProtocolError(RuntimeError):
    pass


ServerRequestHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
NotificationHandler = Callable[[dict[str, Any]], Awaitable[None]]
ResponseSentHandler = Callable[[dict[str, Any], dict[str, Any]], Awaitable[None]]
DisconnectHandler = Callable[[str], None]


async def _ignore_notification(_message: dict[str, Any]) -> None:
    return None


async def _ignore_response_sent(
    _message: dict[str, Any], _response: dict[str, Any]
) -> None:
    return None


@dataclass
class ProtocolClient:
    """Multiplexed, single-reader client for Codex's JSON-RPC-like protocol."""

    ws: Any
    server_request_handler: ServerRequestHandler
    notification_handler: NotificationHandler = _ignore_notification
    response_sent_handler: ResponseSentHandler = _ignore_response_sent
    disconnect_handler: DisconnectHandler | None = None
    max_concurrent_server_requests: int = 128
    next_id: int = 1
    notifications: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=256))
    _pending: dict[int, asyncio.Future[Any]] = field(default_factory=dict, init=False)
    _reader_task: asyncio.Task[None] | None = field(default=None, init=False)
    _request_tasks: set[asyncio.Task[None]] = field(default_factory=set, init=False)
    _send_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
    disconnected: asyncio.Event = field(default_factory=asyncio.Event, init=False)
    connection_error: ProtocolError | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        if self.max_concurrent_server_requests < 1:
            raise ValueError("server request limit must be positive")

    async def start(self) -> None:
        if self._reader_task is None:
            self._reader_task = asyncio.create_task(
                self._reader_loop(), name="codex-app-server-reader"
            )

    async def close(self) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(ProtocolError("app-server client closed"))
        if self._reader_task:
            self._reader_task.cancel()
        for task in self._request_tasks:
            task.cancel()
        await asyncio.gather(
            *(task for task in [self._reader_task, *self._request_tasks] if task),
            return_exceptions=True,
        )

    async def drain_requests(self, *, timeout: float = 3) -> None:
        """Let server-initiated requests send fail-closed replies before close."""
        tasks = tuple(self._request_tasks)
        if not tasks:
            return
        try:
            await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout)
        except asyncio.TimeoutError:
            return

    async def send(self, message: dict[str, Any]) -> None:
        async with self._send_lock:
            await self.ws.send(json.dumps(message, separators=(",", ":")))

    async def call(
        self, method: str, params: dict[str, Any], timeout: float | None = 30
    ) -> Any:
        await self.start()
        request_id = self.next_id
        self.next_id += 1
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            await self.send({"method": method, "id": request_id, "params": params})
            if timeout is None:
                return await future
            return await asyncio.wait_for(future, timeout=timeout)
        finally:
            self._pending.pop(request_id, None)

    async def _reader_loop(self) -> None:
        try:
            while True:
                message = json.loads(await self.ws.recv())
                await self.dispatch(message)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            error = ProtocolError(f"app-server connection closed: {exc}")
            self.connection_error = error
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(error)
        finally:
            self.disconnected.set()
            if self.disconnect_handler is not None:
                try:
                    self.disconnect_handler(str(self.connection_error or "connection closed"))
                except Exception:
                    # Connection teardown must finish even if an observer fails.
                    pass

    async def dispatch(self, message: dict[str, Any]) -> None:
        if "method" in message and "id" in message:
            if len(self._request_tasks) >= self.max_concurrent_server_requests:
                await self.send({
                    "id": message["id"],
                    "error": {"code": -32000, "message": "server request limit reached"},
                })
                return
            task = asyncio.create_task(self._handle_server_request(message))
            self._request_tasks.add(task)
            task.add_done_callback(self._request_tasks.discard)
            return
        if "id" in message:
            future = self._pending.get(message["id"])
            if future and not future.done():
                if "error" in message:
                    future.set_exception(ProtocolError(json.dumps(message["error"], sort_keys=True)))
                else:
                    future.set_result(message.get("result"))
            return
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        self.notifications.append({
            "method": message.get("method"),
            "threadId": params.get("threadId"),
            "turnId": params.get("turnId"),
        })
        await self.notification_handler(message)

    async def _handle_server_request(self, message: dict[str, Any]) -> None:
        try:
            result = await self.server_request_handler(message)
        except Exception as exc:
            await self.send({
                "id": message["id"],
                "error": {"code": -32000, "message": str(exc)},
            })
            return
        await self.send({"id": message["id"], "result": result})
        try:
            await self.response_sent_handler(message, result)
        except Exception:
            # A failed audit callback cannot retract the wire response. The
            # missing sent event makes strict outcome verification fail closed.
            return

    async def initialize(self) -> Any:
        result = await self.call("initialize", {
            "clientInfo": {"name": "codex-coordinator", "version": "0.1.0"},
            "capabilities": {"experimentalApi": True, "requestAttestation": False},
        })
        await self.send({"method": "initialized"})
        return result

    async def drain(self, seconds: float) -> None:
        """Compatibility shim: the background reader is always draining."""
        await self.start()
        await asyncio.sleep(seconds)
