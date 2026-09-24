"""Opt-in live compatibility exercise against the host Codex app-server.

This consumes Codex usage. The compatibility mode uses two projects whose local
`.codex` settings are intentionally distinct. The optional parent-monitor mode
uses a third configured coordination workspace to exercise delegated waiting.
Include a child prompt that causes a valid approval request when automatic
acceptance evidence is required.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
from pathlib import Path
from typing import Any, Mapping

from .api import Coordinator
from .config import OperatorConfig
from .service import HttpControlServer, control_listener


def parent_monitor_prompt(args: argparse.Namespace, control_socket: Path) -> str:
    tasks = {
        args.first: args.first_prompt,
        args.second: args.second_prompt,
    }
    return f"""Exercise the coordination-workspace monitoring guidance against
the live service over Unix socket {control_socket}. Use curl with
`--unix-socket {control_socket}` and URL base `http://localhost` for every
request. Socket access is outside the workspace sandbox: both you and the
monitor must request `sandbox_permissions="require_escalated"` on the first
socket command instead of trying an unprivileged call first. You are the main
parent. Create one child session for each project/task in this JSON object:

{json.dumps(tasks, indent=2, sort_keys=True)}

Delegate all routine GET /events waiting and GET /sessions reconciliation to
the project-scoped `coordinator_monitor` custom subagent. Spawn exactly one
subagent with task name `coordinator_monitor`, no inherited parent history, and
a small brief containing only the
service URL, cursor, created session IDs/project map, recovery rules, and this
reporting contract. The monitor must use GET /events?after=N&wait=30, retain
its cursor and session IDs, continue silently on timeout, never use shell
sleeps or busy polling, recover a 410 through GET /sessions and
recovery.resumeAfter, and report only actionable progress, terminal state, or
a monitoring failure. The monitor must include its timeout count and last safe
cursor in its report. It must remain active until both sessions are terminal
and must not send empty, timeout, or non-actionable progress messages.

You retain all task decisions, follow-ups, cancellation, user questions, and
final verification. Do not personally call GET /events or perform routine
session reconciliation. Do not create a replacement coordinator. Wait until
both requested child sessions are terminal. Collaboration `wait_agent` calls
are how you await monitor reports and are not main-context event waits. Count
`mainContextWaitCalls` only as HTTP GET /events requests issued directly by the
main parent, never as collaboration waits or GET /health calls. Fail visibly if
the monitor fails. After spawning the monitor, call `wait_agent` exactly once
with a timeout long enough to cover the remaining exercise; do not use a series
of short collaboration waits. The monitor's single terminal handoff must cover
both sessions.

End with exactly one JSON object having this shape, using observed values:
{{
  "monitoringEvidence": {{
    "monitorSpawned": true,
    "boundedBrief": true,
    "sessionIds": {{"{args.first}": "...", "{args.second}": "..."}},
    "lastCursor": 0,
    "timeoutCount": 0,
    "reports": [
      {{"kind": "terminal", "sessionId": "...", "state": "completed"}}
    ],
    "mainContextWaitCalls": 0,
    "shellSleepCalls": 0
  }}
}}
"""


def monitoring_evidence(text: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    for offset, character in enumerate(text):
        if character != "{":
            continue
        try:
            candidate, _ = decoder.raw_decode(text[offset:])
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict) and isinstance(
            candidate.get("monitoringEvidence"), dict
        ):
            return candidate["monitoringEvidence"]
    raise RuntimeError("parent did not return the required monitoringEvidence JSON")


def validate_monitoring_evidence(
    evidence: Mapping[str, Any], sessions: Mapping[str, Any], projects: set[str],
) -> None:
    if evidence.get("monitorSpawned") is not True or evidence.get("boundedBrief") is not True:
        raise RuntimeError(
            "parent did not confirm a dedicated monitor with a bounded brief; "
            f"reported evidence: {json.dumps(evidence, sort_keys=True)}"
        )
    if evidence.get("mainContextWaitCalls") != 0:
        raise RuntimeError("parent reported main-context event waiting")
    if evidence.get("shellSleepCalls") != 0:
        raise RuntimeError("parent or monitor reported shell sleep polling")
    if not isinstance(evidence.get("lastCursor"), int) or isinstance(
        evidence.get("lastCursor"), bool
    ):
        raise RuntimeError("monitor did not return its last safe cursor")
    if not isinstance(evidence.get("timeoutCount"), int) or isinstance(
        evidence.get("timeoutCount"), bool
    ):
        raise RuntimeError("monitor did not return its timeout count")
    session_ids = evidence.get("sessionIds")
    if not isinstance(session_ids, dict) or set(session_ids) != projects:
        raise RuntimeError("monitor evidence did not map both requested projects")
    for project, session_id in session_ids.items():
        session = sessions.get(session_id)
        if session is None or session.project != project:
            raise RuntimeError(f"monitor evidence named an unknown {project!r} session")
        if session.state != "completed":
            raise RuntimeError(f"monitored child {project!r} ended in {session.state!r}")
    reports = evidence.get("reports")
    terminal_ids = {
        report.get("sessionId")
        for report in reports if isinstance(report, dict) and report.get("kind") == "terminal"
    } if isinstance(reports, list) else set()
    if terminal_ids != set(session_ids.values()):
        raise RuntimeError("monitor did not return one terminal report for each child")


def parent_rollout_evidence(thread_id: str) -> dict[str, Any]:
    session_root = Path.home() / ".codex" / "sessions"
    matches = list(session_root.rglob(f"*{thread_id}.jsonl"))
    if len(matches) != 1:
        raise RuntimeError(
            f"expected one parent rollout for {thread_id}, found {len(matches)}"
        )
    wait_calls = 0
    monitor_handoffs = 0
    monitor_spawns = 0
    last_turn_usage: dict[str, Any] | None = None
    for line in matches[0].read_text().splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        payload = record.get("payload")
        if not isinstance(payload, dict):
            continue
        if (
            record.get("type") == "response_item"
            and payload.get("name") == "wait_agent"
        ):
            wait_calls += 1
        if (
            record.get("type") == "response_item"
            and payload.get("name") == "spawn_agent"
        ):
            try:
                arguments = json.loads(payload.get("arguments", ""))
            except (json.JSONDecodeError, TypeError):
                arguments = {}
            if (
                arguments.get("task_name") == "coordinator_monitor"
                and arguments.get("agent_type") == "coordinator_monitor"
            ):
                monitor_spawns += 1
        if (
            record.get("type") == "response_item"
            and payload.get("type") == "agent_message"
            and isinstance(payload.get("author"), str)
            and payload["author"].startswith("/root/")
            and payload.get("recipient") == "/root"
        ):
            monitor_handoffs += 1
        if (
            record.get("type") == "token_usage_record"
            and payload.get("thread_id") == thread_id
            and isinstance(payload.get("turn_token_usage"), dict)
        ):
            last_turn_usage = payload["turn_token_usage"]
    if wait_calls != 1:
        raise RuntimeError(
            "parent rollout resumed through "
            f"{wait_calls} wait_agent calls; expected one terminal wait"
        )
    if monitor_spawns != 1:
        raise RuntimeError(
            "parent rollout spawned "
            f"{monitor_spawns} typed project-scoped coordinator monitors; expected one"
        )
    if last_turn_usage is None:
        raise RuntimeError("parent rollout contained no token-usage evidence")
    return {
        "rolloutPath": str(matches[0]),
        "waitAgentCalls": wait_calls,
        "coordinatorMonitorSpawns": monitor_spawns,
        "monitorNotifications": monitor_handoffs,
        "turnTokenUsage": last_turn_usage,
    }


async def run_parent_monitor(
    args: argparse.Namespace, coordinator: Coordinator,
) -> tuple[list[Any], dict[str, Any]]:
    http = HttpControlServer(coordinator.service)
    with tempfile.TemporaryDirectory(prefix="codex-coordinator-live-") as directory:
        control_socket = Path(directory) / "control.sock"
        async with control_listener(
            http.handle, host="127.0.0.1", port=0, unix_socket=control_socket,
        ):
            parent = await coordinator.start(
                args.parent,
                parent_monitor_prompt(args, control_socket),
                effort=args.parent_effort,
            )
            try:
                parent_result = await parent.wait(timeout=args.timeout)
            finally:
                await http.drain()
    if parent_result.state != "completed":
        raise RuntimeError(f"live parent turn failed: {parent_result.state}")
    parent_session = coordinator.service.sessions[parent.id]
    message = parent_session.evidence.get("lastMessage", {}).get("text")
    if not isinstance(message, str):
        raise RuntimeError("live parent returned no final message")
    evidence = monitoring_evidence(message)
    validate_monitoring_evidence(
        evidence, coordinator.service.sessions, {args.first, args.second},
    )
    rollout = parent_rollout_evidence(parent.thread_id)
    child_sessions = [
        coordinator.service.sessions[evidence["sessionIds"][project]]
        for project in (args.first, args.second)
    ]
    parent_turn_starts = sum(
        event.get("method") == "turn/started" and event.get("sessionId") == parent.id
        for event in coordinator.service.raw_events.events
    )
    return child_sessions, {
        "parentSessionId": parent.id,
        "parentThreadId": parent.thread_id,
        "parentTurnStarts": parent_turn_starts,
        "parentRollout": rollout,
        "monitoringEvidence": evidence,
    }


async def run(args) -> dict:
    config = OperatorConfig.load(path=args.config)
    if args.first == args.second:
        raise ValueError("live E2E requires two distinct configured project names")
    if args.parent and args.parent in {args.first, args.second}:
        raise ValueError("live parent project must be distinct from both child projects")
    observed = []
    async with await Coordinator.connect(config) as coordinator:
        async def watch():
            async for event in coordinator.events(after=coordinator.event_cursor):
                observed.append(dict(event.data))

        watcher = asyncio.create_task(watch())
        try:
            if args.parent:
                sessions, parent_evidence = await run_parent_monitor(args, coordinator)
            else:
                first, second = await asyncio.gather(
                    coordinator.start(args.first, args.first_prompt),
                    coordinator.start(args.second, args.second_prompt),
                )
                await asyncio.wait_for(
                    asyncio.gather(first.wait(), second.wait()), args.timeout
                )
                sessions = [
                    coordinator.service.sessions[first.id],
                    coordinator.service.sessions[second.id],
                ]
                parent_evidence = None
        finally:
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)
    states = [session.state for session in sessions]
    if any(state != "completed" for state in states):
        raise RuntimeError(f"live child turn failed: {states}")
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
    summary = {
        "projects": {
            session.project: session.project_path for session in sessions
        },
        "states": states,
        "autoApprovals": len(approvals),
        "effectiveModels": [session.effective_model for session in sessions],
        "effectiveReasoningEfforts": [
            session.effective_reasoning_effort for session in sessions
        ],
    }
    if parent_evidence is not None:
        summary["parentMonitor"] = parent_evidence
    return summary


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--first", required=True)
    parser.add_argument("--second", required=True)
    parser.add_argument(
        "--parent",
        help="configured coordination-workspace project used for the live parent/monitor exercise",
    )
    parser.add_argument("--parent-effort")
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
