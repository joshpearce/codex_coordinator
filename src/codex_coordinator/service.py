"""Loopback HTTP control plane for a long-lived Codex app-server client."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any
from urllib.parse import parse_qs, urlsplit

import websockets

from .coordinator import (
    ApprovalCase,
    ApprovalPolicy,
    JudgeDecision,
    JudgedApprovalHandler,
    SessionRegistration,
    WorkerPermissions,
    mutable_evidence,
)
from .daemon import ensure_daemon
from .protocol import ProtocolClient


@dataclass
class EventLog:
    events: list[dict[str, Any]] = field(default_factory=list)

    def emit(self, event_type: str, **data: Any) -> dict[str, Any]:
        event = {"sequence": len(self.events) + 1, "type": event_type, **data}
        self.events.append(event)
        print(json.dumps(event, sort_keys=True, separators=(",", ":")), flush=True)
        return event

    def after(self, sequence: int) -> list[dict[str, Any]]:
        return self.events[sequence:]


@dataclass
class Session:
    id: str
    thread_id: str
    project: str
    state: str = "active"
    turn_id: str | None = None
    sandbox_policy: Mapping[str, Any] = field(default_factory=dict, repr=False)

    def json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "threadId": self.thread_id,
            "project": self.project,
            "state": self.state,
            "turnId": self.turn_id,
        }


@dataclass(frozen=True)
class PendingApproval:
    registration: SessionRegistration
    case: ApprovalCase
    future: asyncio.Future[dict[str, Any]]


class ApprovalBroker:
    """Live adapter for the same deterministic boundary used by one-shot flows."""

    def __init__(self, events: EventLog) -> None:
        self.events = events
        self.registrations: dict[str, SessionRegistration] = {}
        self.items: dict[tuple[str, str], dict[str, Any]] = {}
        self.pending: dict[str, PendingApproval] = {}
        self.unmanaged_request_count = 0

    def register(self, registration: SessionRegistration) -> None:
        existing = self.registrations.get(registration.thread_id)
        if existing is not None and existing != registration:
            raise ValueError("thread is already bound to a different session")
        self.registrations[registration.thread_id] = registration

    async def __call__(self, message: dict[str, Any]) -> dict[str, Any]:
        method = message.get("method") if isinstance(message, dict) else ""
        params = message.get("params") if isinstance(message, dict) else None
        thread_id = params.get("threadId") if isinstance(params, dict) else None
        registration = self.registrations.get(thread_id) if isinstance(thread_id, str) else None
        if registration is None:
            self.unmanaged_request_count += 1
            return JudgedApprovalHandler._deny(method)
        item_id = params.get("itemId")
        item = self.items.get((thread_id, item_id)) if isinstance(item_id, str) else None
        try:
            case = registration.policy.normalize(
                message,
                session_id=registration.session_id,
                thread_id=registration.thread_id,
                item=item,
            )
        except ValueError as exc:
            self.events.emit(
                "approval.rejected",
                sessionId=registration.session_id,
                threadId=registration.thread_id,
                method=method,
                reason=str(exc),
            )
            return JudgedApprovalHandler._deny(method)
        approval_id = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self.pending[approval_id] = PendingApproval(registration, case, future)
        self.events.emit(
            "approval.requested",
            approvalId=approval_id,
            sessionId=registration.session_id,
            threadId=registration.thread_id,
            method=case.method,
            project=registration.project,
            request=mutable_evidence(case.request),
            declaredIntent=mutable_evidence(case.declared_intent),
            enforcedCapabilities=mutable_evidence(case.enforced_capabilities),
        )
        try:
            # Deliberately no timeout: the coordinating agent owns the lifetime.
            return await future
        finally:
            self.pending.pop(approval_id, None)

    def resolve(
        self,
        approval_id: str,
        session_id: str,
        verdict: str,
        reason: str = "",
        permissions: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if verdict not in {"approve_once", "approve_session", "deny"}:
            raise ValueError(
                "verdict must be approve_once, approve_session, or deny"
            )
        pending = self.pending.get(approval_id)
        if pending is None:
            raise KeyError("unknown or already resolved approval")
        if not isinstance(session_id, str) or session_id != pending.registration.session_id:
            raise ValueError("approval does not belong to this session")
        if not isinstance(reason, str):
            raise ValueError("reason must be a string")
        if not reason.strip():
            raise ValueError("reason must not be empty")
        decision = pending.registration.policy.constrain(
            pending.case, JudgeDecision(verdict, reason, permissions)
        )
        response = JudgedApprovalHandler._encode(pending.case, decision)
        pending.future.set_result(response)
        self.events.emit(
            "approval.resolved",
            approvalId=approval_id,
            sessionId=pending.registration.session_id,
            threadId=pending.registration.thread_id,
            verdict=decision.verdict,
            reason=decision.reason,
            response=response,
            declaredIntent=mutable_evidence(pending.case.declared_intent),
            enforcedCapabilities=mutable_evidence(pending.case.enforced_capabilities),
        )
        return response


class CoordinatorService:
    def __init__(
        self,
        client: ProtocolClient,
        approvals: ApprovalBroker,
        events: EventLog,
        *,
        worker_model: str | None = None,
        worker_reasoning_effort: str = "low",
        allow_session_approval: bool = False,
    ) -> None:
        self.client = client
        self.approvals = approvals
        self.events = events
        self.sessions: dict[str, Session] = {}
        self.thread_sessions: dict[str, str] = {}
        self.stopping = asyncio.Event()
        self.worker_model = worker_model
        self.worker_reasoning_effort = worker_reasoning_effort
        self.allow_session_approval = allow_session_approval

    async def start_session(self, project_value: str, prompt: str) -> dict[str, Any]:
        project = Path(project_value).expanduser().resolve()
        permissions = WorkerPermissions.from_project(project)
        start_params = {
            "cwd": str(project),
            "runtimeWorkspaceRoots": [str(project)],
            "historyMode": "paginated",
            "approvalPolicy": permissions.approval_policy,
            "approvalsReviewer": permissions.approvals_reviewer,
            "sandbox": permissions.sandbox_mode,
            "reasoningEffort": self.worker_reasoning_effort,
        }
        if self.worker_model:
            start_params["model"] = self.worker_model
        result = await self.client.call("thread/start", start_params)
        thread = (result or {}).get("thread", result or {})
        thread_id = str(thread.get("id") or thread.get("threadId") or "")
        if not thread_id:
            raise RuntimeError("thread/start returned no thread ID")
        session_id = uuid.uuid4().hex
        raw_sandbox_policy = permissions.enforced_sandbox(project)
        sandbox_policy = MappingProxyType({
            key: tuple(value) if isinstance(value, list) else value
            for key, value in raw_sandbox_policy.items()
        })
        session = Session(session_id, thread_id, str(project), sandbox_policy=sandbox_policy)
        self.sessions[session_id] = session
        self.thread_sessions[thread_id] = session_id
        policy = ApprovalPolicy(
            project,
            sandbox_mode=permissions.sandbox_mode,
            allow_session_approval=self.allow_session_approval,
        )
        self.approvals.register(SessionRegistration(session_id, thread_id, str(project), policy))
        turn_result = await self.client.call("turn/start", {
            "threadId": thread_id,
            "cwd": str(project),
            "input": [{"type": "text", "text": prompt}],
            "turnTrigger": "coordinator-api",
            "sandboxPolicy": dict(sandbox_policy),
        })
        turn = (turn_result or {}).get("turn", turn_result or {})
        session.turn_id = str(turn.get("id") or turn.get("turnId") or "") or None
        self.events.emit("session.started", session=session.json(), prompt=prompt)
        return session.json()

    async def send_message(self, session_id: str, prompt: str) -> dict[str, Any]:
        session = self.sessions[session_id]
        if session.state == "active":
            raise ValueError("session already has an active turn")
        result = await self.client.call("turn/start", {
            "threadId": session.thread_id,
            "cwd": session.project,
            "input": [{"type": "text", "text": prompt}],
            "turnTrigger": "coordinator-api",
            "sandboxPolicy": dict(session.sandbox_policy),
        })
        turn = (result or {}).get("turn", result or {})
        session.turn_id = str(turn.get("id") or turn.get("turnId") or "") or None
        session.state = "active"
        self.events.emit("session.turn_started", session=session.json(), prompt=prompt)
        return session.json()

    async def notification(self, message: dict[str, Any]) -> None:
        params = message.get("params") or {}
        thread_id = str(params.get("threadId") or params.get("thread_id") or "")
        session_id = self.thread_sessions.get(thread_id)
        method = str(message.get("method", "notification"))
        if session_id is None:
            return
        if method == "item/started":
            item = params.get("item") or {}
            item_id = str(item.get("id") or "")
            if thread_id and item_id:
                self.approvals.items[(thread_id, item_id)] = dict(item)
        if method == "turn/completed" and session_id:
            turn = params.get("turn") or {}
            status = turn.get("status", "completed")
            self.sessions[session_id].state = str(status)
        self.events.emit(
            "app_server.notification",
            method=method,
            sessionId=session_id,
            message=message,
        )


class HttpControlServer:
    def __init__(self, service: CoordinatorService) -> None:
        self.service = service

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request_line = await reader.readline()
            if not request_line:
                return
            method, target, _version = request_line.decode().strip().split(" ", 2)
            headers: dict[str, str] = {}
            while (line := await reader.readline()) not in {b"\r\n", b"\n", b""}:
                key, value = line.decode().split(":", 1)
                headers[key.lower()] = value.strip()
            length = int(headers.get("content-length", "0"))
            body = json.loads((await reader.readexactly(length)).decode()) if length else {}
            status, result = await self.route(method, target, body)
        except (KeyError, ValueError, json.JSONDecodeError) as exc:
            status, result = 400, {"error": str(exc)}
        except Exception as exc:
            status, result = 500, {"error": str(exc)}
        payload = json.dumps(result, sort_keys=True).encode()
        reason = {200: "OK", 201: "Created", 202: "Accepted", 400: "Bad Request", 404: "Not Found", 500: "Internal Server Error"}.get(status, "OK")
        writer.write(
            f"HTTP/1.1 {status} {reason}\r\nContent-Type: application/json\r\nContent-Length: {len(payload)}\r\nConnection: close\r\n\r\n".encode()
            + payload
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    async def route(self, method: str, target: str, body: dict[str, Any]) -> tuple[int, Any]:
        parsed = urlsplit(target)
        parts = [part for part in parsed.path.split("/") if part]
        if method == "GET" and parts == ["health"]:
            return 200, {"ok": True}
        if method == "GET" and parts == ["events"]:
            after = int(parse_qs(parsed.query).get("after", ["0"])[0])
            return 200, {"events": self.service.events.after(after)}
        if method == "GET" and parts == ["sessions"]:
            return 200, {"sessions": [item.json() for item in self.service.sessions.values()]}
        if method == "POST" and parts == ["sessions"]:
            return 201, await self.service.start_session(body["project"], body["prompt"])
        if method == "POST" and len(parts) == 3 and parts[0] == "sessions" and parts[2] == "messages":
            return 201, await self.service.send_message(parts[1], body["prompt"])
        if method == "POST" and len(parts) == 2 and parts[0] == "approvals":
            if set(body) - {"sessionId", "verdict", "reason", "permissions"}:
                raise ValueError("unsupported approval resolution fields")
            response = self.service.approvals.resolve(
                parts[1], body["sessionId"], body["verdict"], body.get("reason", ""),
                body.get("permissions"),
            )
            return 200, response
        if method == "POST" and parts == ["shutdown"]:
            self.service.stopping.set()
            return 202, {"stopping": True}
        return 404, {"error": "not found"}


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--worker-model", default="gpt-5.6-luna")
    parser.add_argument("--worker-reasoning-effort", default="low")
    parser.add_argument(
        "--allow-session-approval",
        action="store_true",
        help="allow session-scoped decisions when a request explicitly offers them",
    )
    parser.add_argument(
        "--socket",
        type=Path,
        default=Path.home() / ".codex/app-server-control/app-server-control.sock",
    )
    return parser.parse_args()


async def run(args: argparse.Namespace) -> None:
    await ensure_daemon(socket_path=args.socket)
    events = EventLog()
    approvals = ApprovalBroker(events)
    async with websockets.unix_connect(
        str(args.socket), uri="ws://localhost/", compression=None,
        open_timeout=10, close_timeout=3, max_size=32 * 1024 * 1024,
    ) as ws:
        service_ref: CoordinatorService | None = None

        async def notification(message: dict[str, Any]) -> None:
            assert service_ref is not None
            await service_ref.notification(message)

        client = ProtocolClient(ws, approvals, notification)
        service_ref = CoordinatorService(
            client,
            approvals,
            events,
            worker_model=args.worker_model,
            worker_reasoning_effort=args.worker_reasoning_effort,
            allow_session_approval=args.allow_session_approval,
        )
        await client.initialize()
        server = await asyncio.start_server(HttpControlServer(service_ref).handle, args.host, args.port)
        actual_port = server.sockets[0].getsockname()[1]
        events.emit("service.started", host=args.host, port=actual_port, pid=os.getpid())
        async with server:
            await service_ref.stopping.wait()
        await client.close()


def main() -> None:
    try:
        asyncio.run(run(arguments()))
    except KeyboardInterrupt:
        print(json.dumps({"type": "service.stopped", "reason": "interrupt"}), flush=True)
        sys.exit(130)


if __name__ == "__main__":
    main()
