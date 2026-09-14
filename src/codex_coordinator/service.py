"""Loopback HTTP control plane for a long-lived Codex app-server client."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import sys
import uuid
from functools import partial
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any
from urllib.parse import parse_qs, urlsplit

import websockets

from .coordinator import (
    ApprovalCase,
    ApprovalPolicy,
    Constitution,
    JudgeDecision,
    Judge,
    JudgedApprovalHandler,
    OneShotCodexJudge,
    SessionRegistration,
    WorkerPermissions,
    codex_exec_json_runner,
    select_worker_permissions,
    mutable_evidence,
)
from .config import OperatorConfig
from .compatibility import check_codex_compatibility
from .daemon import ensure_daemon
from .protocol import ProtocolClient


@dataclass
class EventLog:
    MAX_STDOUT_FIELD_CHARS = 256
    events: list[dict[str, Any]] = field(default_factory=list)
    capacity: int = 2048
    max_bytes: int = 8 * 1024 * 1024
    max_event_bytes: int = 1024 * 1024
    verbose_output: bool = False
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
            summary.update({key: event[key] for key in ("host", "port", "pid") if key in event})
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


@dataclass
class Session:
    id: str
    thread_id: str
    project: str
    state: str = "active"
    turn_id: str | None = None
    last_completed_turn_id: str | None = field(default=None, repr=False)
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
    rpc_request_id: int | str | None


class ApprovalBroker:
    """Live adapter for the same deterministic boundary used by one-shot flows."""

    def __init__(
        self, events: EventLog, *, approval_timeout_seconds: float = 300,
        judge: Judge | None = None,
        constitution: Constitution | None = None,
        item_capacity: int = 256,
        item_max_bytes: int = 64 * 1024,
    ) -> None:
        if not math.isfinite(approval_timeout_seconds) or approval_timeout_seconds <= 0:
            raise ValueError("approval timeout must be positive and finite")
        self.events = events
        self.approval_timeout_seconds = approval_timeout_seconds
        self.judge = judge
        self.constitution = constitution
        if item_capacity < 1 or item_max_bytes < 256:
            raise ValueError("item retention limits must be positive")
        self.item_capacity = item_capacity
        self.item_max_bytes = item_max_bytes
        self.registrations: dict[str, SessionRegistration] = {}
        self.items: OrderedDict[tuple[str, str, str], dict[str, Any]] = OrderedDict()
        self.pending: dict[str, PendingApproval] = {}
        self.unmanaged_request_count = 0
        self.closed = False

    def _policy_provenance(self, project: str) -> dict[str, Any] | None:
        """Record which policy tiers a judge for ``project`` was given, if any."""
        if self.constitution is None:
            return None
        return self.constitution.provenance(project)

    def register(self, registration: SessionRegistration) -> None:
        if self.closed:
            raise RuntimeError("approval broker is closed")
        existing = self.registrations.get(registration.thread_id)
        if existing is not None and existing != registration:
            raise ValueError("thread is already bound to a different session")
        self.registrations[registration.thread_id] = registration

    async def __call__(self, message: dict[str, Any]) -> dict[str, Any]:
        method = message.get("method") if isinstance(message, dict) else ""
        if self.closed:
            return JudgedApprovalHandler._deny(method)
        params = message.get("params") if isinstance(message, dict) else None
        thread_id = params.get("threadId") if isinstance(params, dict) else None
        registration = self.registrations.get(thread_id) if isinstance(thread_id, str) else None
        if registration is None:
            self.unmanaged_request_count += 1
            return JudgedApprovalHandler._deny(method)
        item_id = params.get("itemId")
        turn_id = params.get("turnId")
        item = self.items.get((thread_id, turn_id, item_id)) if isinstance(item_id, str) and isinstance(turn_id, str) else None
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
        code = registration.policy.decide_by_code(case)
        if code is not None and code.verdict != "judge":
            # Decided by the trusted boundary, not by a judge: no pending
            # approval is created, no model is called, and an acceptance is a
            # single-turn accept. The event carries the same evidence an
            # approval would, plus the rule that decided it, so the audit
            # trail stays complete whether the answer was yes or no.
            decision = code.decision
            response = JudgedApprovalHandler._encode(case, decision)
            exec_policy = registration.policy.exec_policy
            extra: dict[str, Any] = {}
            if code.exec_policy is not None:
                extra["execPolicy"] = {
                    **(exec_policy.provenance() if exec_policy is not None else {}),
                    **code.exec_policy.json(),
                }
            if code.paths:
                extra["containment"] = code.json()
            self.events.emit(
                code.event,
                rpcRequestId=self._rpc_request_id(message),
                sessionId=registration.session_id,
                threadId=registration.thread_id,
                method=case.method,
                project=registration.project,
                reason=decision.reason,
                request=mutable_evidence(case.request),
                declaredIntent=mutable_evidence(case.declared_intent),
                enforcedCapabilities=mutable_evidence(case.enforced_capabilities),
                response=response,
                **extra,
            )
            return response
        approval_id = uuid.uuid4().hex
        if len(self.pending) >= 128:
            self.events.emit(
                "approval.rejected", sessionId=registration.session_id,
                threadId=registration.thread_id, method=method,
                reason="pending approval limit reached",
            )
            return JudgedApprovalHandler._deny(method)
        future = asyncio.get_running_loop().create_future()
        self.pending[approval_id] = PendingApproval(
            registration, case, future, self._rpc_request_id(message)
        )
        recorded = self.events.emit(
            "approval.requested",
            approvalId=approval_id,
            rpcRequestId=self._rpc_request_id(message),
            sessionId=registration.session_id,
            threadId=registration.thread_id,
            method=case.method,
            project=registration.project,
            request=mutable_evidence(case.request),
            declaredIntent=mutable_evidence(case.declared_intent),
            enforcedCapabilities=mutable_evidence(case.enforced_capabilities),
            policy=self._policy_provenance(registration.project),
            # Present only when an operator rule sent a case a containment rule
            # would otherwise have decided, so an audit can tell an escalated
            # in-project change from one judging was always going to see.
            **({"containment": code.json()} if code is not None else {}),
        )
        if recorded.get("truncated"):
            self.pending.pop(approval_id, None)
            self.events.emit(
                "approval.rejected", sessionId=case.session_id,
                threadId=case.thread_id, approvalId=approval_id,
                reason="approval evidence exceeds event limit",
            )
            return JudgedApprovalHandler._deny(case.method)
        judge_task = (
            asyncio.create_task(self._judge(approval_id, case))
            if self.judge is not None else None
        )
        try:
            return await asyncio.wait_for(
                asyncio.shield(future), self.approval_timeout_seconds
            )
        except asyncio.TimeoutError:
            if future.done():
                return future.result()
            response = JudgedApprovalHandler._deny(case.method)
            future.set_result(response)
            self.events.emit(
                "approval.expired", approvalId=approval_id,
                sessionId=registration.session_id, threadId=registration.thread_id,
                response=response,
            )
            return response
        finally:
            if judge_task is not None:
                judge_task.cancel()
                await asyncio.gather(judge_task, return_exceptions=True)
            self.pending.pop(approval_id, None)

    async def _judge(self, approval_id: str, case: ApprovalCase) -> None:
        assert self.judge is not None
        try:
            decision = await self.judge.decide(case)
            if not isinstance(decision, JudgeDecision):
                raise ValueError("judge returned no valid decision")
            self.resolve(
                approval_id, case.session_id, decision.verdict,
                decision.reason, mutable_evidence(decision.permissions)
                if decision.permissions is not None else None,
            )
        except (KeyError, asyncio.CancelledError):
            return
        except Exception:
            try:
                self.resolve(approval_id, case.session_id, "deny", "judge failed")
            except KeyError:
                pass

    def close(self, reason: str = "service shutdown") -> None:
        """Deny unresolved approvals before the transport is closed."""
        self.closed = True
        for approval_id, pending in tuple(self.pending.items()):
            if pending.future.done():
                continue
            response = JudgedApprovalHandler._deny(pending.case.method)
            pending.future.set_result(response)
            self.events.emit(
                "approval.cancelled", approvalId=approval_id,
                sessionId=pending.registration.session_id,
                threadId=pending.registration.thread_id,
                reason=reason, response=response,
            )

    def cancel_session(self, session_id: str, reason: str = "session cancelled") -> None:
        for approval_id, pending in tuple(self.pending.items()):
            if pending.registration.session_id != session_id or pending.future.done():
                continue
            response = JudgedApprovalHandler._deny(pending.case.method)
            pending.future.set_result(response)
            self.events.emit(
                "approval.cancelled", approvalId=approval_id,
                sessionId=session_id, threadId=pending.registration.thread_id,
                reason=reason, response=response,
            )

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
        if pending is None or pending.future.done():
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
            rpcRequestId=pending.rpc_request_id,
            sessionId=pending.registration.session_id,
            threadId=pending.registration.thread_id,
            verdict=decision.verdict,
            reason=decision.reason,
            response=response,
            declaredIntent=mutable_evidence(pending.case.declared_intent),
            enforcedCapabilities=mutable_evidence(pending.case.enforced_capabilities),
            policy=self._policy_provenance(pending.registration.project),
        )
        return response

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
            sessionId=registration.session_id,
            threadId=registration.thread_id,
            method=message.get("method"),
            response=mutable_evidence(response),
        )


class CoordinatorService:
    def __init__(
        self,
        client: ProtocolClient,
        approvals: ApprovalBroker,
        events: EventLog,
        *,
        worker_model: str | None = None,
        worker_reasoning_effort: str = "low",
        worker_approval_policy: str | None = None,
        worker_permissions: Mapping[Path, WorkerPermissions] | None = None,
        allow_session_approval: bool = False,
        allowed_roots: Sequence[Path] = (),
        permission_ceilings: Mapping[Path, Mapping[str, Any]] | None = None,
        constitution: Constitution | None = None,
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
        if worker_approval_policy is not None and (
            worker_approval_policy not in WorkerPermissions.WIRE_APPROVAL_POLICIES
        ):
            raise ValueError("unsupported worker approval policy")
        self.worker_approval_policy = worker_approval_policy
        # The boundary a project runs under is operator-owned: it is declared
        # outside every worker-writable root and sent explicitly on the wire.
        # Nothing inside a worker project is consulted.
        self.default_worker_permissions = WorkerPermissions(
            approval_policy=worker_approval_policy or WorkerPermissions().approval_policy,
            source="operator-wide default",
        )
        self.allow_session_approval = allow_session_approval
        self.constitution = constitution
        roots: list[Path] = []
        for root in allowed_roots:
            canonical = Path(root).expanduser().resolve(strict=True)
            if not canonical.is_dir():
                raise ValueError(f"allowed root is not a directory: {canonical}")
            roots.append(canonical)
        self.allowed_roots = tuple(roots)
        ceilings: dict[Path, dict[str, Any]] = {}
        for project, ceiling in (permission_ceilings or {}).items():
            canonical = Path(project).expanduser().resolve(strict=True)
            if not self._within_allowed_roots(canonical):
                raise ValueError(f"permission ceiling project is outside allowed roots: {canonical}")
            if not isinstance(ceiling, Mapping):
                raise ValueError("permission ceiling must be a mapping")
            copied = mutable_evidence(ceiling)
            ApprovalPolicy(canonical, allowed_permissions=copied)
            ceilings[canonical] = copied
        self.permission_ceilings = MappingProxyType(ceilings)
        declarations: dict[Path, WorkerPermissions] = {}
        for project, permissions in (worker_permissions or {}).items():
            canonical = Path(project).expanduser().resolve(strict=True)
            if not self._within_allowed_roots(canonical):
                raise ValueError(f"worker permissions project is outside allowed roots: {canonical}")
            if not isinstance(permissions, WorkerPermissions):
                raise ValueError("worker permissions must be a WorkerPermissions value")
            if not permissions.exec_policy_loaded:
                raise ValueError(
                    f"{permissions.source}: exec_policy is declared but no rules were loaded"
                )
            declarations[canonical] = permissions
        self.worker_permissions = MappingProxyType(declarations)

    def permissions_for(self, project: Path) -> WorkerPermissions:
        return select_worker_permissions(
            project, self.worker_permissions, self.default_worker_permissions,
        )

    def _within_allowed_roots(self, project: Path) -> bool:
        return any(project == root or root in project.parents for root in self.allowed_roots)

    async def start_session(self, project_value: str, prompt: str) -> dict[str, Any]:
        if self.stopping.is_set() or self.approvals.closed:
            raise RuntimeError("service is stopping or disconnected")
        if len(self.sessions) >= 1024:
            raise ValueError("session limit reached; restart the local service")
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt.encode()) > 1024 * 1024:
            raise ValueError("prompt must be nonempty text no larger than 1 MiB")
        if not isinstance(project_value, str) or not project_value.strip():
            raise ValueError("project must be a nonempty path")
        try:
            project = Path(project_value).expanduser().resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ValueError(f"project path is unavailable: {project_value}") from exc
        if not project.is_dir() or not self._within_allowed_roots(project):
            raise ValueError("project is outside the configured allowed roots")
        if self.constitution is not None and not self.constitution.covers(project):
            raise ValueError(
                "no project constitution governs this project; add one under "
                "[project_constitutions] in the operator configuration"
            )
        permissions = self.permissions_for(project)
        policy = ApprovalPolicy(
            project,
            sandbox_mode=permissions.sandbox_mode,
            allow_session_approval=self.allow_session_approval,
            allowed_permissions=self.permission_ceilings.get(project),
            exec_policy=permissions.exec_policy,
        )
        start_params = {
            "cwd": str(project),
            "runtimeWorkspaceRoots": [str(project)],
            # Operator configuration decides how much reaches a judge. It is
            # sent on the wire rather than left in the project, both because
            # anything inside the project is writable by the worker it governs
            # and because the pinned CLI rejects "untrusted" as a config value.
            "approvalPolicy": permissions.approval_policy,
            "approvalsReviewer": permissions.approvals_reviewer,
            "sandbox": permissions.sandbox_mode,
        }
        if self.worker_model:
            start_params["model"] = self.worker_model
        result = await self.client.call("thread/start", start_params)
        thread = (result or {}).get("thread", result or {})
        thread_id = thread.get("id") or thread.get("threadId")
        if not isinstance(thread_id, str) or not thread_id.strip():
            raise RuntimeError("thread/start returned no valid thread ID")
        if thread_id in self.thread_sessions:
            raise RuntimeError(f"thread/start returned an already registered thread ID: {thread_id}")
        session_id = uuid.uuid4().hex
        raw_sandbox_policy = permissions.enforced_sandbox(project)
        sandbox_policy = MappingProxyType({
            key: tuple(value) if isinstance(value, list) else value
            for key, value in raw_sandbox_policy.items()
        })
        session = Session(session_id, thread_id, str(project), sandbox_policy=sandbox_policy)
        self.approvals.register(SessionRegistration(session_id, thread_id, str(project), policy))
        self.sessions[session_id] = session
        self.thread_sessions[thread_id] = session_id
        try:
            turn_result = await self.client.call("turn/start", {
                "threadId": thread_id,
                "cwd": str(project),
                "input": [{"type": "text", "text": prompt}],
                "turnTrigger": "coordinator-api",
                "effort": self.worker_reasoning_effort,
                "sandboxPolicy": dict(sandbox_policy),
            })
        except Exception:
            session.state = "failed"
            self.events.emit("session.start_failed", session=session.json())
            raise
        turn = (turn_result or {}).get("turn", turn_result or {})
        if session.state == "active":
            session.turn_id = str(turn.get("id") or turn.get("turnId") or "") or None
        self.events.emit(
            "session.started", session=session.json(), prompt=prompt,
            workerPermissions=permissions.provenance(),
        )
        return session.json()

    async def send_message(self, session_id: str, prompt: str) -> dict[str, Any]:
        if self.stopping.is_set() or self.approvals.closed:
            raise RuntimeError("service is stopping or disconnected")
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt.encode()) > 1024 * 1024:
            raise ValueError("prompt must be nonempty text no larger than 1 MiB")
        session = self.sessions[session_id]
        if session.state == "active":
            raise ValueError("session already has an active turn")
        if session.state != "completed":
            raise ValueError("follow-up requires a completed turn")
        session.state = "active"
        session.turn_id = None
        try:
            result = await self.client.call("turn/start", {
                "threadId": session.thread_id,
                "cwd": session.project,
                "input": [{"type": "text", "text": prompt}],
                "turnTrigger": "coordinator-api",
                "effort": self.worker_reasoning_effort,
                "sandboxPolicy": dict(session.sandbox_policy),
            })
        except Exception:
            session.state = "failed"
            self.events.emit("session.turn_start_failed", session=session.json())
            raise
        turn = (result or {}).get("turn", result or {})
        if session.state == "active":
            session.turn_id = str(turn.get("id") or turn.get("turnId") or "") or None
        self.events.emit("session.turn_started", session=session.json(), prompt=prompt)
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
        if method == "item/started":
            item = params.get("item")
            if not isinstance(item, Mapping):
                item = {}
            item_id = item.get("id")
            turn_id = params.get("turnId")
            if thread_id and isinstance(turn_id, str) and turn_id and isinstance(item_id, str) and item_id and len(json.dumps(item).encode()) <= self.approvals.item_max_bytes:
                self.approvals.items[(thread_id, turn_id, item_id)] = dict(item)
                if len(self.approvals.items) > self.approvals.item_capacity:
                    self.approvals.items.popitem(last=False)
        if method == "serverRequest/resolved":
            request_id = params.get("requestId")
            if isinstance(request_id, (int, str)) and not isinstance(request_id, bool):
                self.events.emit(
                    "approval.server_resolved", rpcRequestId=request_id,
                    sessionId=session_id, threadId=thread_id,
                )
        if method == "item/completed":
            item = params.get("item")
            completed_item_id = item.get("id") if isinstance(item, Mapping) else None
            completed_turn_id = params.get("turnId")
            if isinstance(completed_item_id, str) and isinstance(completed_turn_id, str):
                self.approvals.items.pop((thread_id, completed_turn_id, completed_item_id), None)
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
                for key in tuple(self.approvals.items):
                    if key[0] == thread_id and (
                        not isinstance(completed_turn_id, str) or key[1] == completed_turn_id
                    ):
                        self.approvals.items.pop(key, None)
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
        self.events.emit(
            "app_server.notification",
            method=method,
            sessionId=session_id,
            message=message,
        )


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
            reason = {200: "OK", 201: "Created", 202: "Accepted", 400: "Bad Request", 403: "Forbidden", 404: "Not Found", 408: "Request Timeout", 410: "Gone", 413: "Payload Too Large", 431: "Request Header Fields Too Large", 500: "Internal Server Error"}.get(status, "OK")
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
        if method == "POST" and len(parts) == 3 and parts[0] == "sessions" and parts[2] == "cancel":
            return 202, await self.service.cancel_session(parts[1])
        if method == "POST" and len(parts) == 2 and parts[0] == "approvals":
            if self.service.approvals.judge is not None:
                return 403, {"error": "service-owned judging does not accept HTTP verdicts"}
            if set(body) - {"sessionId", "verdict", "reason", "permissions"}:
                raise ValueError("unsupported approval resolution fields")
            response = self.service.approvals.resolve(
                parts[1], body["sessionId"], body["verdict"], body.get("reason", ""),
                body.get("permissions"),
            )
            return 200, response
        if method == "POST" and parts == ["shutdown"]:
            await self.service.shutdown()
            return 202, {"stopping": True}
        return 404, {"error": "not found"}


class RequestTooLarge(ValueError):
    def __init__(self, status: int, reason: str) -> None:
        self.status = status
        super().__init__(reason)


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--config", type=Path, help="trusted operator TOML configuration")
    parser.add_argument(
        "--allowed-root", type=Path, action="append",
        help="canonical parent directory for worker projects; repeat for multiple roots",
    )
    parser.add_argument("--codex-command")
    parser.add_argument("--worker-model")
    parser.add_argument("--worker-reasoning-effort")
    parser.add_argument("--approval-timeout-seconds", type=float)
    parser.add_argument("--event-capacity", type=int)
    parser.add_argument("--event-max-bytes", type=int)
    parser.add_argument("--item-capacity", type=int)
    parser.add_argument("--item-max-bytes", type=int)
    parser.add_argument(
        "--allow-session-approval",
        action="store_true", default=None,
        help="allow session-scoped decisions when a request explicitly offers them",
    )
    parser.add_argument("--socket", type=Path)
    parser.add_argument(
        "--verbose-events", action="store_true",
        help="log full managed event payloads to stdout (may contain sensitive data)",
    )
    return parser.parse_args()


async def run(args: argparse.Namespace) -> None:
    config = OperatorConfig.load(path=args.config, overrides={
        "allowed_roots": args.allowed_root,
        "codex_command": args.codex_command,
        "socket_path": args.socket,
        "worker_model": args.worker_model,
        "worker_reasoning_effort": args.worker_reasoning_effort,
        "allow_session_approval": args.allow_session_approval,
        "approval_timeout_seconds": args.approval_timeout_seconds,
        "event_capacity": args.event_capacity,
        "event_max_bytes": args.event_max_bytes,
        "item_capacity": args.item_capacity,
        "item_max_bytes": args.item_max_bytes,
    })
    if not config.allowed_roots:
        raise ValueError("configure at least one allowed root before starting the service")
    if config.approval_mode is None:
        raise ValueError("configure approval_mode = 'service' or 'external' before starting the service")
    if args.host != "127.0.0.1":
        raise ValueError("the unauthenticated service is restricted to 127.0.0.1")
    await asyncio.to_thread(check_codex_compatibility, config.codex_command)
    await ensure_daemon(
        socket_path=config.socket_path, codex_command=config.codex_command,
    )
    events = EventLog(
        capacity=config.event_capacity, max_bytes=config.event_max_bytes,
        verbose_output=args.verbose_events,
    )
    judge = None
    if config.approval_mode == "service":
        assert config.constitution is not None
        judge = OneShotCodexJudge(
            partial(
                codex_exec_json_runner,
                codex_command=config.codex_command,
                timeout_seconds=config.judge_timeout_seconds,
            ),
            constitution=config.constitution,
        )
    approvals = ApprovalBroker(
        events, approval_timeout_seconds=config.approval_timeout_seconds,
        judge=judge, constitution=config.constitution,
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
            worker_approval_policy=config.worker_approval_policy,
            worker_permissions=config.worker_permissions,
            allow_session_approval=config.allow_session_approval,
            allowed_roots=config.allowed_roots,
            permission_ceilings=config.permission_ceilings,
            constitution=config.constitution,
        )
        client.disconnect_handler = service_ref.connection_lost
        try:
            await client.initialize()
            server = await asyncio.start_server(HttpControlServer(service_ref).handle, args.host, args.port)
            actual_port = server.sockets[0].getsockname()[1]
            events.emit("service.started", host=args.host, port=actual_port, pid=os.getpid())
            async with server:
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
