"""Opt-in live compatibility exercise against the host Codex app-server.

This consumes Codex usage. Configure two projects whose local `.codex` settings
are intentionally distinct, and include a prompt that causes a valid approval
request so the resulting event log proves automatic acceptance.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from .api import Coordinator
from .config import OperatorConfig


async def run(args) -> dict:
    config = OperatorConfig.load(path=args.config)
    if args.first == args.second:
        raise ValueError("live E2E requires two distinct configured project names")
    observed = []
    async with await Coordinator.connect(config) as coordinator:
        async def watch():
            async for event in coordinator.events(after=coordinator.event_cursor):
                observed.append(dict(event.data))

        watcher = asyncio.create_task(watch())
        try:
            first, second = await asyncio.gather(
                coordinator.start(args.first, args.first_prompt),
                coordinator.start(args.second, args.second_prompt),
            )
            results = await asyncio.wait_for(
                asyncio.gather(first.wait(), second.wait()), args.timeout
            )
        finally:
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)
    if any(result.state != "completed" for result in results):
        raise RuntimeError(f"live child turn failed: {[result.state for result in results]}")
    sessions = [coordinator.service.sessions[first.id], coordinator.service.sessions[second.id]]
    if args.expected_model and any(
        session.effective_model != args.expected_model for session in sessions
    ):
        raise RuntimeError(
            "host model setting was not inherited: "
            f"{[session.effective_model for session in sessions]}"
        )
    expected_efforts = [args.first_expected_effort, args.second_expected_effort]
    for session, expected in zip(sessions, expected_efforts):
        if expected and session.effective_reasoning_effort != expected:
            raise RuntimeError(
                f"project {session.project} did not inherit reasoning effort {expected!r}; "
                f"app-server reported {session.effective_reasoning_effort!r}"
            )
    if args.unrestricted_marker:
        marker = args.unrestricted_marker.expanduser().resolve(strict=False)
        if not marker.is_file() or marker.read_text().strip() != args.unrestricted_marker_text:
            raise RuntimeError(
                "unrestricted child did not create the requested external marker; "
                "its project configuration may have been narrowed"
            )
    approvals = [event for event in observed if event.get("type") == "approval.auto_approved"]
    if args.require_approval and not approvals:
        raise RuntimeError("no valid child approval request was automatically accepted")
    return {
        "projects": {
            first.project: first.project_path,
            second.project: second.project_path,
        },
        "states": [result.state for result in results],
        "autoApprovals": len(approvals),
        "effectiveModels": [session.effective_model for session in sessions],
        "effectiveReasoningEfforts": [
            session.effective_reasoning_effort for session in sessions
        ],
    }


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--first", required=True)
    parser.add_argument("--second", required=True)
    parser.add_argument("--first-prompt", required=True)
    parser.add_argument("--second-prompt", required=True)
    parser.add_argument("--require-approval", action="store_true")
    parser.add_argument("--expected-model")
    parser.add_argument("--first-expected-effort")
    parser.add_argument("--second-expected-effort")
    parser.add_argument("--unrestricted-marker", type=Path)
    parser.add_argument("--unrestricted-marker-text", default="codex-coordinator-live-ok")
    parser.add_argument("--timeout", type=float, default=600)
    return parser.parse_args()


def main() -> None:
    print(json.dumps(asyncio.run(run(arguments())), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
