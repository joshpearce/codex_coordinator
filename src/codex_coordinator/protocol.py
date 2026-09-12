from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any


class ProtocolError(RuntimeError):
    pass


ServerRequestHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
NotificationHandler = Callable[[dict[str, Any]], Awaitable[None]]


async def _ignore_notification(_message: dict[str, Any]) -> None:
    return None


@dataclass
class ProtocolClient:
    """Multiplexed, single-reader client for Codex's JSON-RPC-like protocol."""

    ws: Any
    server_request_handler: ServerRequestHandler
    notification_handler: NotificationHandler = _ignore_notification
    next_id: int = 1
    notifications: list[dict[str, Any]] = field(default_factory=list)
    _pending: dict[int, asyncio.Future[Any]] = field(default_factory=dict, init=False)
    _reader_task: asyncio.Task[None] | None = field(default=None, init=False)
    _request_tasks: set[asyncio.Task[None]] = field(default_factory=set, init=False)
    _send_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)

    async def start(self) -> None:
        if self._reader_task is None:
            self._reader_task = asyncio.create_task(
                self._reader_loop(), name="codex-app-server-reader"
            )

    async def close(self) -> None:
        if self._reader_task:
            self._reader_task.cancel()
        for task in self._request_tasks:
            task.cancel()
        await asyncio.gather(
            *(task for task in [self._reader_task, *self._request_tasks] if task),
            return_exceptions=True,
        )

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
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(error)

    async def dispatch(self, message: dict[str, Any]) -> None:
        if "method" in message and "id" in message:
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
        self.notifications.append(message)
        await self.notification_handler(message)

    async def _handle_server_request(self, message: dict[str, Any]) -> None:
        try:
            result = await self.server_request_handler(message)
            await self.send({"id": message["id"], "result": result})
        except Exception as exc:
            await self.send({
                "id": message["id"],
                "error": {"code": -32000, "message": str(exc)},
            })

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
