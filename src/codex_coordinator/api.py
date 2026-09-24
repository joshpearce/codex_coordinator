"""Python-facing local coordination API.

The coordinator owns one app-server connection and transparently starts child
threads in configured projects. Valid approval requests are accepted automatically.
"""

from __future__ import annotations

import asyncio
import copy
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import Any

import websockets

from .config import OperatorConfig
from .compatibility import check_codex_compatibility
from .daemon import validate_local_socket
from .protocol import ProtocolClient
from .service import AutomaticApprovalHandler, CoordinatorService, EventLog


@dataclass(frozen=True)
class CoordinationEvent:
    sequence: int
    type: str
    session_id: str | None
    data: Mapping[str, Any]

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "CoordinationEvent":
        session = record.get("session")
        session_id = record.get("sessionId") or (
            session.get("id") if isinstance(session, Mapping) else None
        )
        return cls(record["sequence"], record["type"], session_id, copy.deepcopy(dict(record)))

@dataclass(frozen=True)
class TerminalResult:
    session_id: str
    thread_id: str
    state: str
    turn_id: str | None


@dataclass(frozen=True)
class SessionHandle:
    coordinator: "Coordinator"
    id: str
    thread_id: str
    project: str
    project_path: str

    async def follow_up(self, prompt: str, *, effort: str | None = None) -> None:
        await self.coordinator.follow_up(self.id, prompt, effort=effort)

    async def cancel(self) -> None:
        await self.coordinator.cancel(self.id)

    async def wait(self, *, timeout: float | None = None) -> TerminalResult:
        return await self.coordinator.wait(self.id, timeout=timeout)


class Coordinator:
    """Single-user, local coordinator for configured child projects.

    Use ``async with await Coordinator.connect(config) as coordinator``.
    Events are in-memory and bounded; an evicted cursor raises EventCursorExpired.
    Completion means a turn ended, not that the thread can never receive a follow-up.
    """

    TERMINAL = frozenset({"completed", "failed", "interrupted", "cancelled", "cancel_unknown", "connection_lost", "protocol_unknown", "shutdown_unknown"})

    def __init__(
        self, service: CoordinatorService, approvals: AutomaticApprovalHandler,
        events: EventLog, client: ProtocolClient, transport: Any = None,
    ) -> None:
        self.service = service
        self.approvals = approvals
        self.event_log = events
        self.client = client
        self.client.disconnect_handler = self.service.connection_lost
        if self.client.disconnected.is_set():
            self.service.connection_lost(str(self.client.connection_error or "connection closed"))
        self._transport = transport
        self._closed = False

    @classmethod
    async def connect(
        cls, config: OperatorConfig, *, state_path: Any = None,
    ) -> "Coordinator":
        if not config.projects:
            raise ValueError("configure at least one named project")
        await asyncio.to_thread(check_codex_compatibility, config.codex_command)
        try:
            validate_local_socket(config.socket_path)
        except (OSError, ValueError) as exc:
            raise ConnectionError(
                f"host Codex app-server socket is unavailable or invalid: {config.socket_path}; "
                "start the host app-server before connecting"
            ) from exc
        try:
            transport = await websockets.unix_connect(
                str(config.socket_path), uri="ws://localhost/", compression=None,
                open_timeout=10, close_timeout=3, max_size=32 * 1024 * 1024,
            )
        except (OSError, TimeoutError, websockets.exceptions.WebSocketException) as exc:
            raise ConnectionError(
                f"cannot connect to Codex app-server socket {config.socket_path}; "
                "check the listener and run codex-coordinator-preflight"
            ) from exc
        events = EventLog(
            capacity=config.event_capacity, max_bytes=config.event_max_bytes,
        )
        approvals = AutomaticApprovalHandler(
            events, item_capacity=config.item_capacity,
            item_max_bytes=config.item_max_bytes,
        )
        client = ProtocolClient(transport, approvals)
        client.response_sent_handler = approvals.response_sent
        service = CoordinatorService(
            client, approvals, events,
            worker_model=config.worker_model,
            worker_reasoning_effort=config.worker_reasoning_effort,
            projects=config.projects,
            state_path=state_path,
        )
        client.notification_handler = service.notification
        try:
            await client.initialize()
            await service.recover_sessions()
        except BaseException:
            await client.close()
            await transport.close()
            raise
        return cls(service, approvals, events, client, transport)

    async def __aenter__(self) -> "Coordinator":
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.close()

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("coordinator is closed")
        if self.client.disconnected.is_set():
            self.service.connection_lost(str(self.client.connection_error or "connection closed"))
            raise RuntimeError("app-server connection lost")

    @property
    def event_cursor(self) -> int:
        """Latest emitted event sequence for a bounded catch-up wait."""
        return self.event_log.next_sequence - 1

    async def start(self, project: str, prompt: str, *, model: str | None = None, effort: str | None = None) -> SessionHandle:
        self._ensure_open()
        session = await self.service.start_session(project, prompt, effort, model)
        return SessionHandle(self, session["id"], session["threadId"], session["project"], session["projectPath"])

    async def follow_up(self, session_id: str, prompt: str, *, effort: str | None = None) -> None:
        self._ensure_open()
        await self.service.send_message(session_id, prompt, effort)

    async def cancel(self, session_id: str) -> None:
        self._ensure_open()
        await self.service.cancel_session(session_id)

    async def wait(self, session_id: str, *, timeout: float | None = None) -> TerminalResult:
        if self._closed:
            raise RuntimeError("coordinator is closed")
        if self.client.disconnected.is_set():
            self.service.connection_lost(str(self.client.connection_error or "connection closed"))

        async def observe() -> TerminalResult:
            cursor = self.event_log.next_sequence - 1
            while True:
                session = self.service.sessions[session_id]
                if self.client.disconnected.is_set():
                    self.service.connection_lost(str(self.client.connection_error or "connection closed"))
                if session.state in self.TERMINAL:
                    return TerminalResult(session.id, session.thread_id, session.state, session.turn_id)
                try:
                    events = await self.event_log.wait_after(cursor, timeout=0.5)
                except asyncio.TimeoutError:
                    continue
                if events:
                    cursor = events[-1]["sequence"]

        try:
            return await asyncio.wait_for(observe(), timeout)
        except asyncio.TimeoutError:
            raise TimeoutError(f"session {session_id} did not reach a terminal state") from None

    async def events(self, *, after: int = 0) -> AsyncIterator[CoordinationEvent]:
        cursor = after
        while not self._closed:
            records = await self.event_log.wait_after(cursor)
            for record in records:
                cursor = record["sequence"]
                yield CoordinationEvent.from_record(record)

    async def close(self) -> None:
        if self._closed:
            return
        if self.client.disconnected.is_set():
            self.service.connection_lost(str(self.client.connection_error or "connection closed"))
        self._closed = True
        try:
            await self.service.shutdown()
            self.event_log.emit("coordinator.closed")
        finally:
            await self.client.close()
            if self._transport is not None:
                await self._transport.close()
