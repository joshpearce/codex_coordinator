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
    codex_exec_json_runner,
)
from .daemon import ensure_daemon
from .protocol import ProtocolClient


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("project", type=Path)
    parser.add_argument("prompt")
    parser.add_argument("judge_policy")
    parser.add_argument("--timeout", type=float, default=300)
    return parser.parse_args()


async def run(args: argparse.Namespace) -> dict:
    project = args.project.expanduser().resolve()
    socket_path = Path.home() / ".codex/app-server-control/app-server-control.sock"
    await ensure_daemon(socket_path=socket_path)

    events: list[dict] = []

    def record(case, decision, response) -> None:
        events.append({
            "method": case.method,
            "threadId": case.thread_id,
            "request": dict(case.request),
            "verdict": decision.verdict,
            "reason": decision.reason,
            "response": dict(response),
        })

    judge = OneShotCodexJudge(
        partial(codex_exec_json_runner, timeout_seconds=args.timeout),
        policy_instructions=args.judge_policy,
    )
    policy = ApprovalPolicy(project)
    approvals = JudgedApprovalHandler(project, policy, judge, on_decision=record)
    async with websockets.unix_connect(
        str(socket_path), uri="ws://localhost/", compression=None,
        open_timeout=10, close_timeout=3, max_size=32 * 1024 * 1024,
    ) as ws:
        client = ProtocolClient(ws, approvals)
        await client.initialize()
        supervisor = JudgedSessionSupervisor(client, approvals, project)
        thread_id = await supervisor.start(args.prompt)
        state = await supervisor.monitor(thread_id, max_seconds=args.timeout)
        return {"threadId": thread_id, "state": state, "decisions": events}


def main() -> None:
    print(json.dumps(asyncio.run(run(arguments())), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
