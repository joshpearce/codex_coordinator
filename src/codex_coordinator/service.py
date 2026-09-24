"""Loopback HTTP control plane for a long-lived Codex app-server client."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import stat
import sys
import uuid
from contextlib import asynccontextmanager
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import websockets

from .config import OperatorConfig
from .compatibility import check_codex_compatibility
from .daemon import validate_local_socket
from .protocol import ProtocolClient, ProtocolError


@dataclass
class EventLog:
    MAX_STDOUT_FIELD_CHARS = 256
    events: list[dict[str, Any]] = field(default_factory=list)
    capacity: int = 2048
    max_bytes: int = 8 * 1024 * 1024
    max_event_bytes: int = 1024 * 1024
    verbose_output: bool = False
    output_enabled: bool = True
    service_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    next_sequence: int = 1
    _changed: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    _sizes: list[int] = field(default_factory=list, repr=False)
    _retained_bytes: int = field(default=0, repr=False)

    def __post_init__(self) -> None:
        if self.capacity < 1 or self.max_bytes < 256 or self.max_event_bytes < 256:
            raise ValueError("event retention limits must be positive")

    def emit(self, event_type: str, **data: Any) -> dict[str, Any]:
        event = self._redact_known({"sequence": self.next_sequence, "type": event_type, **data})
        self.next_sequence += 1
        encoded = json.dumps(event, sort_keys=True, separators=(",", ":"))
        size = len(encoded.encode())
        if size > min(self.max_event_bytes, self.max_bytes):
            event = {
                "sequence": event["sequence"], "type": event_type,
                "truncated": True,
                **{
                    key: data[key]
                    for key in ("sessionId", "threadId", "approvalId")
                    if isinstance(data.get(key), str) and len(data[key]) <= 128
                },
            }
            encoded = json.dumps(event, sort_keys=True, separators=(",", ":"))
            size = len(encoded.encode())
            if size > min(self.max_event_bytes, self.max_bytes):
                event = {
                    "sequence": event["sequence"], "type": "event.truncated",
                    "truncated": True,
                }
                encoded = json.dumps(event, sort_keys=True, separators=(",", ":"))
                size = len(encoded.encode())
        self.events.append(event)
        self._sizes.append(size)
        self._retained_bytes += size
        while len(self.events) > self.capacity or self._retained_bytes > self.max_bytes:
            self.events.pop(0)
            self._retained_bytes -= self._sizes.pop(0)
        self._changed.set()
        self._changed = asyncio.Event()
        if self.output_enabled:
            output = self._log_view(event)
            print(json.dumps(output, sort_keys=True, separators=(",", ":")), flush=True)
        return event

    def _log_view(self, event: dict[str, Any]) -> dict[str, Any]:
        if self.verbose_output:
            return self._redact_known(event)
        summary = {
            key: event[key]
            for key in ("sequence", "type", "sessionId", "threadId", "approvalId", "method", "verdict", "truncated")
            if key in event
        }
        session = event.get("session")
        if isinstance(session, dict):
            summary["sessionId"] = session.get("id")
            summary["state"] = session.get("state")
        if event["type"] == "service.started":
            summary.update({
                key: event[key]
                for key in ("host", "port", "socketPath", "pid")
                if key in event
            })
        return {
            key: value[:self.MAX_STDOUT_FIELD_CHARS] + "…"
            if isinstance(value, str) and len(value) > self.MAX_STDOUT_FIELD_CHARS
            else value
            for key, value in summary.items()
        }

    @classmethod
    def _redact_known(cls, value: Any) -> Any:
        if isinstance(value, dict):
            result: dict[str, Any] = {}
            for key, item in value.items():
                normalized = key.lower().replace("_", "")
                sensitive = normalized in {"env", "environment", "environmentvariables"} or any(
                    marker in normalized
                    for marker in ("authorization", "apikey", "password", "secret", "cookie", "credential", "token")
                )
                result[key] = "[REDACTED]" if sensitive else cls._redact_known(item)
            return result
        if isinstance(value, (list, tuple)):
            return [cls._redact_known(item) for item in value]
        return value

    def after(self, sequence: int) -> list[dict[str, Any]]:
        if sequence < 0:
            raise ValueError("event cursor must be nonnegative")
        latest = self.next_sequence - 1
        if sequence > latest:
            raise EventCursorAhead(latest, self.service_id)
        oldest = self.events[0]["sequence"] if self.events else self.next_sequence
        if sequence < oldest - 1:
            raise EventCursorExpired(oldest)
        return [event for event in self.events if event["sequence"] > sequence]

    async def wait_after(self, sequence: int, timeout: float | None = None) -> list[dict[str, Any]]:
        changed = self._changed
        events = self.after(sequence)
        if events:
            return events
        await asyncio.wait_for(changed.wait(), timeout)
        return self.after(sequence)


class EventCursorExpired(ValueError):
    def __init__(self, oldest_sequence: int) -> None:
        self.oldest_sequence = oldest_sequence
        super().__init__(f"event cursor expired; oldest available sequence is {oldest_sequence}")


class EventCursorAhead(ValueError):
    def __init__(self, latest_sequence: int, service_id: str) -> None:
        self.latest_sequence = latest_sequence
        self.service_id = service_id
        super().__init__(
            "event cursor is ahead of this service generation; "
            f"latest sequence is {latest_sequence}"
        )


@dataclass
class Session:
    id: str
    thread_id: str
    project: str
    project_path: str
    state: str = "active"
    turn_id: str | None = None
    last_completed_turn_id: str | None = field(default=None, repr=False)
    model: str | None = None
    reasoning_effort: str | None = None
    effective_model: str | None = None
    effective_reasoning_effort: str | None = None

    def json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "threadId": self.thread_id,
            "project": self.project,
            "projectPath": self.project_path,
            "state": self.state,
            "turnId": self.turn_id,
            "model": self.model,
            "reasoningEffort": self.reasoning_effort,
            "effectiveModel": self.effective_model,
            "effectiveReasoningEffort": self.effective_reasoning_effort,
        }


class AutomaticApprovalHandler:
    """Approve well-formed requests from registered child threads."""

    def __init__(
        self, events: EventLog, *, item_capacity: int = 256,
        item_max_bytes: int = 64 * 1024,
    ) -> None:
        self.events = events
        if item_capacity < 1 or item_max_bytes < 256:
            raise ValueError("item retention limits must be positive")
        self.item_capacity = item_capacity
        self.item_max_bytes = item_max_bytes
        self.registrations: dict[str, tuple[str, str, str]] = {}
        self.closed = False

    def register(self, thread_id: str, session_id: str, project: str, project_path: str) -> None:
        if self.closed:
            raise RuntimeError("approval handler is closed")
        registration = (session_id, project, project_path)
        existing = self.registrations.get(thread_id)
        if existing is not None and existing != registration:
            raise ValueError("thread is already bound to a different session")
        self.registrations[thread_id] = registration

    async def __call__(self, message: dict[str, Any]) -> dict[str, Any]:
        if self.closed:
            raise ProtocolError("approval handler is closed")
        if not isinstance(message, dict):
            raise ProtocolError("malformed app-server request")
        method = message.get("method")
        params = message.get("params") if isinstance(message, dict) else None
        if not isinstance(method, str) or not isinstance(params, Mapping):
            return self._fail(message, "malformed app-server request")
        thread_id = params.get("threadId")
        registration = self.registrations.get(thread_id) if isinstance(thread_id, str) else None
        if registration is None:
            return self._fail(message, "approval request is not for a managed thread")
        for field_name in ("turnId", "itemId"):
            if not isinstance(params.get(field_name), str) or not params[field_name]:
                return self._fail(message, f"approval request has no valid {field_name}")
        if isinstance(params.get("startedAtMs"), bool) or not isinstance(params.get("startedAtMs"), (int, float)):
            return self._fail(message, "approval request has no valid startedAtMs")
        if method in {"item/commandExecution/requestApproval", "item/fileChange/requestApproval"}:
            offered = params.get("availableDecisions", [])
            if offered is not None and not isinstance(offered, list):
                return self._fail(message, "availableDecisions must be a list")
            decision = "acceptForSession" if "acceptForSession" in (offered or []) else "accept"
            response = {"decision": decision}
        elif method == "item/permissions/requestApproval":
            permissions = params.get("permissions")
            if not isinstance(permissions, Mapping):
                return self._fail(message, "permission approval has no permissions payload")
            response = {"permissions": dict(permissions), "scope": "session", "strictAutoReview": True}
        else:
            return self._fail(message, f"unsupported app-server request method: {method}")
        session_id, project, project_path = registration
        self.events.emit(
            "approval.auto_approved", rpcRequestId=self._rpc_request_id(message),
            sessionId=session_id, threadId=thread_id, method=method,
            project=project, projectPath=project_path, response=response,
        )
        return response

    def _fail(self, message: dict[str, Any], reason: str) -> dict[str, Any]:
        params = message.get("params")
        thread_id = params.get("threadId") if isinstance(params, Mapping) else None
        registration = self.registrations.get(thread_id) if isinstance(thread_id, str) else None
        self.events.emit(
            "approval.protocol_error", rpcRequestId=self._rpc_request_id(message),
            threadId=thread_id, method=message.get("method"), reason=reason,
            **({"sessionId": registration[0]} if registration else {}),
        )
        raise ProtocolError(reason)

    def close(self, reason: str = "service shutdown") -> None:
        self.closed = True

    def cancel_session(self, session_id: str, reason: str = "session cancelled") -> None:
        return None

    @staticmethod
    def _rpc_request_id(message: dict[str, Any]) -> int | str | None:
        value = message.get("id")
        return value if isinstance(value, (int, str)) and not isinstance(value, bool) else None

    async def response_sent(self, message: dict[str, Any], response: dict[str, Any]) -> None:
        """Record only successfully sent managed responses for release verification."""
        params = message.get("params")
        thread_id = params.get("threadId") if isinstance(params, dict) else None
        registration = self.registrations.get(thread_id) if isinstance(thread_id, str) else None
        rpc_id = self._rpc_request_id(message)
        if registration is None or rpc_id is None:
            return
        self.events.emit(
            "approval.wire_sent",
            rpcRequestId=rpc_id,
            sessionId=registration[0],
            threadId=thread_id,
            method=message.get("method"),
            response=response,
        )


class CoordinatorService:
    def __init__(
        self,
        client: ProtocolClient,
        approvals: AutomaticApprovalHandler,
        events: EventLog,
        *,
        worker_model: str | None = None,
        worker_reasoning_effort: str | None = None,
        projects: Mapping[str, Path] | None = None,
        raw_events: EventLog | None = None,
    ) -> None:
        self.client = client
        self.approvals = approvals
        self.events = events
        self.sessions: dict[str, Session] = {}
        self.thread_sessions: dict[str, str] = {}
        self.stopping = asyncio.Event()
        self._connection_lost = False
        self.worker_model = worker_model
        self.worker_reasoning_effort = worker_reasoning_effort
        self.projects = dict(projects or {})
        self.raw_events = raw_events or EventLog(
            capacity=events.capacity, max_bytes=events.max_bytes,
            max_event_bytes=events.max_event_bytes, service_id=events.service_id,
            output_enabled=False,
        )

    def _selected_effort(self, requested: str | None) -> str | None:
        effort = self.worker_reasoning_effort if requested is None else requested
        if effort is not None and (not isinstance(effort, str) or not effort.strip()):
            raise ValueError("reasoning effort must be nonempty text when set")
        return effort

    async def start_session(
        self, project_value: str, prompt: str, effort: str | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        if self.stopping.is_set() or self.approvals.closed:
            raise RuntimeError("service is stopping or disconnected")
        if len(self.sessions) >= 1024:
            raise ValueError("session limit reached; restart the local service")
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt.encode()) > 1024 * 1024:
            raise ValueError("prompt must be nonempty text no larger than 1 MiB")
        if not isinstance(project_value, str) or not project_value.strip():
            raise ValueError("project must be a configured project name")
        selected_effort = self._selected_effort(effort)
        if project_value not in self.projects:
            raise ValueError(f"unknown project: {project_value}")
        project = self.projects[project_value]
        selected_model = self.worker_model if model is None else model
        if selected_model is not None and (not isinstance(selected_model, str) or not selected_model.strip()):
            raise ValueError("model must be nonempty text when set")
        start_params = {"cwd": str(project)}
        if selected_model is not None:
            start_params["model"] = selected_model
        result = await self.client.call("thread/start", start_params)
        thread = (result or {}).get("thread", result or {})
        thread_id = thread.get("id") or thread.get("threadId")
        if not isinstance(thread_id, str) or not thread_id.strip():
            raise RuntimeError("thread/start returned no valid thread ID")
        if thread_id in self.thread_sessions:
            raise RuntimeError(f"thread/start returned an already registered thread ID: {thread_id}")
        session_id = uuid.uuid4().hex
        session = Session(
            session_id, thread_id, project_value, str(project),
            model=selected_model, reasoning_effort=selected_effort,
            effective_model=thread.get("model") if isinstance(thread.get("model"), str) else None,
            effective_reasoning_effort=(
                thread.get("reasoningEffort")
                if isinstance(thread.get("reasoningEffort"), str) else None
            ),
        )
        self.approvals.register(thread_id, session_id, project_value, str(project))
        self.sessions[session_id] = session
        self.thread_sessions[thread_id] = session_id
        turn_params: dict[str, Any] = {
            "threadId": thread_id,
            "input": [{"type": "text", "text": prompt}],
        }
        if selected_effort is not None:
            turn_params["effort"] = selected_effort
        try:
            turn_result = await self.client.call("turn/start", turn_params)
        except Exception:
            session.state = "failed"
            self.events.emit("session.start_failed", session=session.json())
            raise
        turn = (turn_result or {}).get("turn", turn_result or {})
        if session.state == "active":
            session.turn_id = str(turn.get("id") or turn.get("turnId") or "") or None
        self.events.emit(
            "session.started", session=session.json(), prompt=prompt,
        )
        return session.json()

    async def send_message(
        self, session_id: str, prompt: str, effort: str | None = None,
    ) -> dict[str, Any]:
        if self.stopping.is_set() or self.approvals.closed:
            raise RuntimeError("service is stopping or disconnected")
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt.encode()) > 1024 * 1024:
            raise ValueError("prompt must be nonempty text no larger than 1 MiB")
        session = self.sessions[session_id]
        if session.state == "active":
            raise ValueError("session already has an active turn")
        if session.state != "completed":
            raise ValueError("follow-up requires a completed turn")
        selected_effort = self._selected_effort(effort)
        session.state = "active"
        session.turn_id = None
        session.reasoning_effort = selected_effort
        turn_params: dict[str, Any] = {
            "threadId": session.thread_id,
            "input": [{"type": "text", "text": prompt}],
        }
        if selected_effort is not None:
            turn_params["effort"] = selected_effort
        try:
            result = await self.client.call("turn/start", turn_params)
        except Exception:
            session.state = "failed"
            self.events.emit("session.turn_start_failed", session=session.json())
            raise
        turn = (result or {}).get("turn", result or {})
        if session.state == "active":
            session.turn_id = str(turn.get("id") or turn.get("turnId") or "") or None
        self.events.emit(
            "session.turn_started", session=session.json(), prompt=prompt,
        )
        return session.json()

    async def cancel_session(self, session_id: str) -> dict[str, Any]:
        if self.stopping.is_set() or self.approvals.closed:
            raise RuntimeError("service is stopping or disconnected")
        session = self.sessions[session_id]
        if session.state != "active" or not session.turn_id:
            raise ValueError("session has no active turn to cancel")
        self.approvals.cancel_session(session_id)
        session.state = "cancelling"
        self.events.emit("session.cancelling", session=session.json())
        try:
            await self.client.call("turn/interrupt", {
                "threadId": session.thread_id, "turnId": session.turn_id,
            })
        except Exception as exc:
            if session.state == "cancelling":
                session.state = "cancel_unknown"
                self.events.emit("session.cancel_unknown", session=session.json())
            raise RuntimeError(
                f"could not confirm cancellation of session {session_id}; "
                "reconcile its turn with the app-server"
            ) from exc
        return session.json()

    def connection_lost(self, reason: str) -> None:
        if self.stopping.is_set() or self._connection_lost:
            return
        self._connection_lost = True
        self.approvals.close("app-server connection lost")
        self.events.emit("service.connection_lost", reason=reason)
        for session in self.sessions.values():
            if session.state in {"active", "cancelling"}:
                session.state = "connection_lost"
                self.events.emit("session.connection_lost", session=session.json(), reason=reason)

    async def shutdown(self) -> None:
        self.stopping.set()
        self.approvals.close("service shutdown")
        drain_requests = getattr(self.client, "drain_requests", None)
        if drain_requests is not None:
            await drain_requests(timeout=3)
        active = [
            session for session in self.sessions.values()
            if session.state == "active" and session.turn_id
        ]
        interrupts = [
            self.client.call("turn/interrupt", {
                "threadId": session.thread_id, "turnId": session.turn_id,
            })
            for session in active
        ]
        results: list[Any] = []
        if interrupts:
            try:
                results = await asyncio.wait_for(
                    asyncio.gather(*interrupts, return_exceptions=True), timeout=3,
                )
            except asyncio.TimeoutError:
                results = [TimeoutError("turn interrupt timed out")] * len(active)
        uncertain = {
            session.id for session, result in zip(active, results)
            if isinstance(result, BaseException)
        }
        uncertain.update(
            session.id for session in self.sessions.values()
            if session.state == "cancelling" or (session.state == "active" and not session.turn_id)
        )
        for session in self.sessions.values():
            if session.state in {"active", "cancelling"}:
                session.state = "shutdown_unknown" if session.id in uncertain else "cancelled"
                self.events.emit(f"session.{session.state}", session=session.json())

    async def notification(self, message: dict[str, Any]) -> None:
        params = message.get("params")
        if not isinstance(params, Mapping):
            self.events.emit("app_server.malformed_notification", method=message.get("method"))
            return
        raw_thread_id = params.get("threadId") or params.get("thread_id")
        thread_id = raw_thread_id if isinstance(raw_thread_id, str) else ""
        session_id = self.thread_sessions.get(thread_id)
        method = str(message.get("method", "notification"))
        if session_id is None:
            return
        self.raw_events.emit(
            "app_server.notification", method=method,
            sessionId=session_id, threadId=thread_id, message=message,
        )
        if method == "item/started":
            pass
        if method == "serverRequest/resolved":
            request_id = params.get("requestId")
            if isinstance(request_id, (int, str)) and not isinstance(request_id, bool):
                self.events.emit(
                    "approval.server_resolved", rpcRequestId=request_id,
                    sessionId=session_id, threadId=thread_id,
                )
        if method == "item/completed":
            item = params.get("item")
            completed_turn_id = params.get("turnId")
            if isinstance(item, Mapping) and item.get("type") == "commandExecution":
                item_id = item.get("id")
                status = item.get("status")
                turn_id = params.get("turnId")
                if all(isinstance(value, str) for value in (item_id, status, turn_id)):
                    self.events.emit(
                        "approval.command_completed", sessionId=session_id,
                        threadId=thread_id, turnId=turn_id,
                        itemId=item_id, itemStatus=status,
                    )
            if isinstance(item, Mapping) and item.get("type") == "agentMessage":
                text = self._agent_message_text(item)
                self.events.emit(
                    "child.message", sessionId=session_id, threadId=thread_id,
                    turnId=completed_turn_id if isinstance(completed_turn_id, str) else None,
                    itemId=item.get("id") if isinstance(item.get("id"), str) else None,
                    text=text,
                )
        if method == "turn/completed" and session_id:
            turn = params.get("turn")
            status = turn.get("status") if isinstance(turn, Mapping) else None
            completed_turn_id = turn.get("id") if isinstance(turn, Mapping) else None
            session = self.sessions[session_id]
            if session.state in {"active", "cancelling", "cancel_unknown"} and (
                isinstance(completed_turn_id, str) and completed_turn_id
                and completed_turn_id != session.turn_id
                and (session.turn_id is not None or completed_turn_id == session.last_completed_turn_id)
            ):
                self.events.emit(
                    "session.stale_turn_completion", sessionId=session_id,
                    threadId=thread_id, turnId=completed_turn_id,
                )
            elif session.state in {"active", "cancelling", "cancel_unknown"}:
                self.approvals.cancel_session(session_id, "turn completed")
                session.state = (
                    status if isinstance(completed_turn_id, str) and completed_turn_id
                    and isinstance(status, str) and status in {"completed", "failed", "interrupted"}
                    else "protocol_unknown"
                )
                if isinstance(completed_turn_id, str) and completed_turn_id:
                    session.last_completed_turn_id = completed_turn_id
                session.turn_id = None
                if session.state == "protocol_unknown":
                    self.events.emit("session.protocol_unknown", session=session.json())
                else:
                    self.events.emit(
                        f"session.{session.state}", session=session.json(),
                        threadId=thread_id,
                        turnId=completed_turn_id if isinstance(completed_turn_id, str) else None,
                    )

    @staticmethod
    def _agent_message_text(item: Mapping[str, Any]) -> str:
        text = item.get("text")
        if isinstance(text, str):
            return text
        content = item.get("content")
        if isinstance(content, Sequence) and not isinstance(content, (str, bytes)):
            parts = [
                part.get("text") for part in content
                if isinstance(part, Mapping) and isinstance(part.get("text"), str)
            ]
            return "".join(parts)
        return ""


class HttpControlServer:
    MAX_REQUEST_LINE = 8192
    MAX_HEADERS = 32768
    MAX_BODY = 1024 * 1024
    READ_TIMEOUT = 10
    WRITE_TIMEOUT = 10

    def __init__(self, service: CoordinatorService) -> None:
        self.service = service
        self._inflight = 0

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if self._inflight >= 64:
            busy = b'{"error":"server is busy"}'
            try:
                writer.write(
                    b'HTTP/1.1 503 Service Unavailable\r\nContent-Type: application/json\r\n'
                    + f'Content-Length: {len(busy)}\r\nConnection: close\r\n\r\n'.encode()
                    + busy
                )
            except OSError:
                pass
            finally:
                # Best effort only: saturated clients must not accumulate
                # handler tasks while waiting for their response to drain.
                writer.close()
            return
        self._inflight += 1
        try:
            # Bound before the read so the error handler can name the request
            # even when parsing it is what failed.
            method, target = "", ""
            try:
                method, target, body = await asyncio.wait_for(
                    self._read_request(reader), timeout=self.READ_TIMEOUT,
                )
                status, result = await self.route(method, target, body)
            except asyncio.TimeoutError:
                status, result = 408, {"error": "request read timed out"}
            except asyncio.LimitOverrunError:
                status, result = 431, {"error": "request headers too large"}
            except asyncio.IncompleteReadError:
                status, result = 400, {"error": "incomplete request body"}
            except RequestTooLarge as exc:
                status, result = exc.status, {"error": str(exc)}
            except EventCursorExpired as exc:
                status, result = 410, {
                    "error": str(exc), "oldestSequence": exc.oldest_sequence,
                    "serviceId": self.service.events.service_id,
                }
            except EventCursorAhead as exc:
                status, result = 409, {
                    "error": str(exc), "latestSequence": exc.latest_sequence,
                    "serviceId": exc.service_id,
                }
            except (KeyError, ValueError, json.JSONDecodeError) as exc:
                status, result = 400, {"error": str(exc)}
            except Exception as exc:
                # The client gets nothing specific, but an unexpected failure
                # must leave a trace: a 500 with no record is undiagnosable.
                status, result = 500, {"error": "internal server error"}
                self.service.events.emit(
                    "http.internal_error",
                    method=method, path=urlsplit(target).path,
                    error=f"{type(exc).__name__}: {exc}",
                )
            payload = json.dumps(result, sort_keys=True).encode()
            reason = {200: "OK", 201: "Created", 202: "Accepted", 400: "Bad Request", 403: "Forbidden", 404: "Not Found", 408: "Request Timeout", 409: "Conflict", 410: "Gone", 413: "Payload Too Large", 431: "Request Header Fields Too Large", 500: "Internal Server Error"}.get(status, "OK")
            writer.write(
                f"HTTP/1.1 {status} {reason}\r\nContent-Type: application/json\r\nContent-Length: {len(payload)}\r\nConnection: close\r\n\r\n".encode()
                + payload
            )
            try:
                await asyncio.wait_for(writer.drain(), timeout=self.WRITE_TIMEOUT)
            except (OSError, asyncio.TimeoutError):
                pass
        finally:
            self._inflight -= 1
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), timeout=self.WRITE_TIMEOUT)
            except (OSError, asyncio.TimeoutError):
                pass

    async def _read_request(self, reader: asyncio.StreamReader) -> tuple[str, str, dict[str, Any]]:
        request_line = await reader.readline()
        if not request_line:
            raise ValueError("empty request")
        if len(request_line) > self.MAX_REQUEST_LINE:
            raise RequestTooLarge(431, "request line too large")
        method, target, version = request_line.decode().strip().split(" ", 2)
        if version != "HTTP/1.1":
            raise ValueError("HTTP/1.1 is required")
        headers: dict[str, str] = {}
        header_size = 0
        header_count = 0
        while (line := await reader.readline()) not in {b"\r\n", b"\n", b""}:
            header_size += len(line)
            header_count += 1
            if header_size > self.MAX_HEADERS or header_count > 64:
                raise RequestTooLarge(431, "request headers too large")
            key, value = line.decode().split(":", 1)
            if key.lower() in headers:
                raise ValueError("duplicate request header")
            headers[key.lower()] = value.strip()
        if not line:
            raise ValueError("incomplete request headers")
        if "transfer-encoding" in headers:
            raise ValueError("transfer-encoding is unsupported")
        if "origin" in headers:
            raise ValueError("browser-origin requests are unsupported")
        hostname = headers.get("host", "").split(":", 1)[0].lower()
        if hostname not in {"localhost", "127.0.0.1"}:
            raise ValueError("Host must be localhost or 127.0.0.1")
        length = int(headers.get("content-length", "0"))
        if length < 0:
            raise ValueError("negative content length")
        if length > self.MAX_BODY:
            raise RequestTooLarge(413, "request body too large")
        body = json.loads((await reader.readexactly(length)).decode()) if length else {}
        if not isinstance(body, dict):
            raise ValueError("request body must be an object")
        return method, target, body

    async def route(self, method: str, target: str, body: dict[str, Any]) -> tuple[int, Any]:
        parsed = urlsplit(target)
        parts = [part for part in parsed.path.split("/") if part]
        if method == "GET" and parts == ["health"]:
            return 200, {"ok": True, "serviceId": self.service.events.service_id}
        if method == "GET" and parts == ["events"]:
            after = int(parse_qs(parsed.query).get("after", ["0"])[0])
            return 200, {
                "serviceId": self.service.events.service_id,
                "events": self.service.events.after(after),
            }
        if method == "GET" and parts == ["debug", "events"]:
            after = int(parse_qs(parsed.query).get("after", ["0"])[0])
            return 200, {
                "serviceId": self.service.events.service_id,
                "events": self.service.raw_events.after(after),
            }
        if method == "GET" and parts == ["sessions"]:
            return 200, {
                "serviceId": self.service.events.service_id,
                "sessions": [item.json() for item in self.service.sessions.values()],
            }
        if method == "POST" and parts == ["sessions"]:
            if set(body) - {"project", "prompt", "effort", "model"}:
                raise ValueError("unsupported session fields")
            return 201, await self.service.start_session(
                body["project"], body["prompt"], body.get("effort"), body.get("model"),
            )
        if method == "POST" and len(parts) == 3 and parts[0] == "sessions" and parts[2] == "messages":
            if set(body) - {"prompt", "effort"}:
                raise ValueError("unsupported session message fields")
            return 201, await self.service.send_message(
                parts[1], body["prompt"], body.get("effort"),
            )
        if method == "POST" and len(parts) == 3 and parts[0] == "sessions" and parts[2] == "cancel":
            return 202, await self.service.cancel_session(parts[1])
        if method == "POST" and parts == ["shutdown"]:
            await self.service.shutdown()
            return 202, {"stopping": True}
        return 404, {"error": "not found"}


class RequestTooLarge(ValueError):
    def __init__(self, status: int, reason: str) -> None:
        self.status = status
        super().__init__(reason)


def _operator_control_socket(
    raw_path: Path,
    *,
    forbidden_roots: Sequence[Path],
) -> Path:
    if not raw_path.is_absolute():
        raise ValueError("control socket path must be absolute")
    try:
        parent = raw_path.parent.resolve(strict=True)
        parent_info = parent.lstat()
    except OSError as exc:
        raise ValueError("control socket parent must exist") from exc
    if (
        not stat.S_ISDIR(parent_info.st_mode)
        or parent_info.st_uid != os.getuid()
        or parent_info.st_mode & 0o022
    ):
        raise ValueError("control socket parent must be an owner-controlled directory")
    path = parent / raw_path.name
    if path.exists() or path.is_symlink():
        raise ValueError(f"refusing to replace existing control socket path: {path}")
    if any(path == root or root in path.parents for root in forbidden_roots):
        raise ValueError("control socket must be outside coordinator- and worker-writable roots")
    return path


def _remove_control_socket(path: Path, identity: tuple[int, int]) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISSOCK(info.st_mode) or (info.st_dev, info.st_ino) != identity:
        raise RuntimeError(f"control socket changed while the service was running: {path}")
    path.unlink()


@asynccontextmanager
async def control_listener(
    handler: Any,
    *,
    host: str,
    port: int,
    unix_socket: Path | None = None,
    forbidden_roots: Sequence[Path] = (),
):
    socket_path: Path | None = None
    socket_identity: tuple[int, int] | None = None
    if unix_socket is None:
        if host != "127.0.0.1":
            raise ValueError("the unauthenticated service is restricted to 127.0.0.1")
        server = await asyncio.start_server(handler, host, port)
        details = {"host": host, "port": server.sockets[0].getsockname()[1]}
    else:
        socket_path = _operator_control_socket(
            unix_socket, forbidden_roots=forbidden_roots,
        )
        server = await asyncio.start_unix_server(handler, path=socket_path)
        os.chmod(socket_path, 0o600)
        info = socket_path.lstat()
        socket_identity = (info.st_dev, info.st_ino)
        details = {"socketPath": str(socket_path)}
    try:
        yield server, details
    finally:
        server.close()
        await server.wait_closed()
        if socket_path is not None and socket_identity is not None:
            _remove_control_socket(socket_path, socket_identity)


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--unix-socket", type=Path,
        help="serve the HTTP control plane on an owner-controlled Unix socket instead of TCP",
    )
    parser.add_argument("--config", type=Path, help="trusted operator TOML configuration")
    parser.add_argument("--codex-command")
    parser.add_argument("--worker-model")
    parser.add_argument("--worker-reasoning-effort")
    parser.add_argument("--event-capacity", type=int)
    parser.add_argument("--event-max-bytes", type=int)
    parser.add_argument("--item-capacity", type=int)
    parser.add_argument("--item-max-bytes", type=int)
    parser.add_argument("--socket", type=Path)
    parser.add_argument(
        "--verbose-events", action="store_true",
        help="log full managed event payloads to stdout (may contain sensitive data)",
    )
    return parser.parse_args()


async def run(args: argparse.Namespace) -> None:
    config = OperatorConfig.load(path=args.config, overrides={
        "codex_command": args.codex_command,
        "socket_path": args.socket,
        "worker_model": args.worker_model,
        "worker_reasoning_effort": args.worker_reasoning_effort,
        "event_capacity": args.event_capacity,
        "event_max_bytes": args.event_max_bytes,
        "item_capacity": args.item_capacity,
        "item_max_bytes": args.item_max_bytes,
    })
    if not config.projects:
        raise ValueError("configure at least one named project before starting the service")
    await asyncio.to_thread(check_codex_compatibility, config.codex_command)
    try:
        validate_local_socket(config.socket_path)
    except (OSError, ValueError) as exc:
        raise ConnectionError(
            f"host Codex app-server socket is unavailable or invalid: {config.socket_path}; "
            "start the host app-server before starting codex-coordinator"
        ) from exc
    events = EventLog(
        capacity=config.event_capacity, max_bytes=config.event_max_bytes,
        verbose_output=args.verbose_events,
    )
    approvals = AutomaticApprovalHandler(
        events,
        item_capacity=config.item_capacity, item_max_bytes=config.item_max_bytes,
    )
    try:
        transport = await websockets.unix_connect(
            str(config.socket_path), uri="ws://localhost/", compression=None,
            open_timeout=10, close_timeout=3, max_size=32 * 1024 * 1024,
        )
    except (OSError, TimeoutError, websockets.exceptions.WebSocketException) as exc:
        raise ConnectionError(
            f"cannot connect to Codex app-server socket {config.socket_path}; "
            "check the listener and run codex-coordinator-preflight --require-socket"
        ) from exc
    async with transport as ws:
        service_ref: CoordinatorService | None = None

        async def notification(message: dict[str, Any]) -> None:
            assert service_ref is not None
            await service_ref.notification(message)

        client = ProtocolClient(ws, approvals, notification)
        client.response_sent_handler = approvals.response_sent
        service_ref = CoordinatorService(
            client,
            approvals,
            events,
            worker_model=config.worker_model,
            worker_reasoning_effort=config.worker_reasoning_effort,
            projects=config.projects,
        )
        client.disconnect_handler = service_ref.connection_lost
        try:
            await client.initialize()
            async with control_listener(
                HttpControlServer(service_ref).handle,
                host=args.host,
                port=args.port,
                unix_socket=getattr(args, "unix_socket", None),
                forbidden_roots=(),
            ) as (_server, listener_details):
                events.emit(
                    "service.started", serviceId=events.service_id,
                    **listener_details, pid=os.getpid(),
                )
                stop_task = asyncio.create_task(service_ref.stopping.wait())
                disconnect_task = asyncio.create_task(client.disconnected.wait())
                done, pending = await asyncio.wait(
                    {stop_task, disconnect_task}, return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                if disconnect_task in done and not service_ref.stopping.is_set():
                    service_ref.connection_lost(str(client.connection_error or "connection closed"))
        finally:
            if not client.disconnected.is_set() and not service_ref.stopping.is_set():
                await service_ref.shutdown()
            approvals.close()
            await client.close()


def main() -> None:
    try:
        asyncio.run(run(arguments()))
    except KeyboardInterrupt:
        print(json.dumps({"type": "service.stopped", "reason": "interrupt"}), flush=True)
        sys.exit(130)


if __name__ == "__main__":
    main()
