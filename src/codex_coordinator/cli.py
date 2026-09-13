#!/usr/bin/env python3
"""Opt-in live smoke test for a Codex worker judged by one-shot Codex calls."""

from __future__ import annotations

import argparse
import asyncio
import json
from functools import partial
from pathlib import Path

import websockets

from .coordinator import (
    ApprovalPolicy,
    JudgedApprovalHandler,
    JudgedSessionSupervisor,
    OneShotCodexJudge,
    WorkerPermissions,
    codex_exec_json_runner,
    mutable_evidence,
)
from .config import OperatorConfig
from .compatibility import check_codex_compatibility
from .daemon import ensure_daemon
from .protocol import ProtocolClient


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("project", type=Path)
    parser.add_argument("prompt")
    parser.add_argument("judge_policy", nargs="?")
    parser.add_argument("--config", type=Path)
    parser.add_argument(
        "--allowed-root", type=Path, action="append",
        help="operator-approved parent for worker projects; repeat for multiple roots",
    )
    parser.add_argument("--socket", type=Path)
    parser.add_argument("--codex-command")
    parser.add_argument("--worker-model")
    parser.add_argument("--worker-reasoning-effort")
    parser.add_argument("--judge-timeout-seconds", type=float)
    parser.add_argument("--timeout", type=float, default=300)
    return parser.parse_args()


async def run(args: argparse.Namespace) -> dict:
    config = OperatorConfig.load(path=args.config, overrides={
        "allowed_roots": args.allowed_root,
        "socket_path": args.socket,
        "codex_command": args.codex_command,
        "worker_model": args.worker_model,
        "worker_reasoning_effort": args.worker_reasoning_effort,
        "judge_policy": args.judge_policy,
        "judge_timeout_seconds": args.judge_timeout_seconds,
    })
    if not config.allowed_roots:
        raise ValueError("configure at least one allowed root before starting a worker")
    project = args.project.expanduser().resolve(strict=True)
    if not project.is_dir() or not any(
        project == root or root in project.parents for root in config.allowed_roots
    ):
        raise ValueError("project is outside the configured allowed roots")
    await asyncio.to_thread(check_codex_compatibility, config.codex_command)
    await ensure_daemon(
        socket_path=config.socket_path, codex_command=config.codex_command,
    )

    events: list[dict] = []

    def record(case, decision, response) -> None:
        events.append({
            "method": case.method,
            "sessionId": case.session_id,
            "threadId": case.thread_id,
            "request": mutable_evidence(case.request),
            "declaredIntent": mutable_evidence(case.declared_intent),
            "enforcedCapabilities": mutable_evidence(case.enforced_capabilities),
            "verdict": decision.verdict,
            "reason": decision.reason,
            "permissions": dict(decision.permissions) if decision.permissions is not None else None,
            "response": dict(response),
        })

    judge = OneShotCodexJudge(
        partial(
            codex_exec_json_runner,
            codex_command=config.codex_command,
            timeout_seconds=config.judge_timeout_seconds,
        ),
        policy_instructions=config.judge_policy,
    )
    worker_permissions = WorkerPermissions.from_project(project)
    policy = ApprovalPolicy(
        project,
        sandbox_mode=worker_permissions.sandbox_mode,
        allow_session_approval=config.allow_session_approval,
        allowed_permissions=config.permission_ceilings.get(project),
    )
    approvals = JudgedApprovalHandler(project, policy, judge, on_decision=record)
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
        client = ProtocolClient(ws, approvals, approvals.notification)
        try:
            await client.initialize()
            supervisor = JudgedSessionSupervisor(
                client, approvals, project,
                worker_model=config.worker_model,
                worker_reasoning_effort=config.worker_reasoning_effort,
            )
            thread_id = await supervisor.start(args.prompt)
            state = await supervisor.monitor(thread_id, max_seconds=args.timeout)
            return {"threadId": thread_id, "state": state, "decisions": events}
        finally:
            await client.close()


def main() -> None:
    print(json.dumps(asyncio.run(run(arguments())), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
