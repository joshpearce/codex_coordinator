"""Source-only example: coordinate two operator-selected Codex projects.

Install codex-coordinator first. Each project's boundary is declared in the
operator-owned permissions file documented in README.md. For a release gate, provide exact
allow/deny command strings and --require-outcomes; prompts must cause Codex to
request approvals for those commands.
"""

import argparse
import asyncio
import json
from pathlib import Path

from codex_coordinator import Coordinator, JudgeDecision, OperatorConfig
from codex_coordinator.coordinator import mutable_evidence


class ExampleJudge:
    def __init__(self, allow_command=None, deny_command=None):
        self.allow_command = allow_command
        self.deny_command = deny_command
        self._input_lock = asyncio.Lock()

    async def decide(self, case):
        command = case.request.get("command")
        if self.allow_command is not None:
            if command == self.allow_command:
                return JudgeDecision("approve_once", "operator-configured exact command")
            return JudgeDecision("deny", "command is not on the exact allowlist")
        async with self._input_lock:
            print(f"\nApproval for session {case.session_id} in {case.project}")
            print(f"Method: {case.method}")
            print(json.dumps({
                "request": mutable_evidence(case.request),
                "declaredIntent": mutable_evidence(case.declared_intent),
                "enforcedCapabilities": mutable_evidence(case.enforced_capabilities),
            }, indent=2, sort_keys=True))
            answer = await asyncio.to_thread(input, "Approve once? [y/N] ")
        if answer.strip().lower() == "y":
            return JudgeDecision("approve_once", "operator approved once")
        return JudgeDecision("deny", "operator denied")


def summarize_outcomes(events, allow_command, deny_command, session_ids=()):
    valid_sessions = set(session_ids)
    requests = {
        event["approvalId"]: event
        for event in events
        if event["type"] == "approval.requested"
        and (not valid_sessions or event.get("sessionId") in valid_sessions)
    }
    resolved = [
        event for event in events
        if event["type"] == "approval.resolved"
        and event.get("approvalId") in requests
        and event.get("sessionId") == requests[event["approvalId"]].get("sessionId")
        and event.get("threadId") == requests[event["approvalId"]].get("threadId")
    ]
    sent = {
        (event.get("rpcRequestId"), event.get("sessionId"), event.get("threadId")): event
        for event in events
        if event["type"] == "approval.wire_sent" and event.get("rpcRequestId") is not None
    }
    wire_resolved = [
        event for event in resolved
        if event.get("rpcRequestId") is not None
        and (wire := sent.get((
            event["rpcRequestId"], event.get("sessionId"), event.get("threadId")
        ))) is not None
        and wire.get("response") == event.get("response")
        and requests[event["approvalId"]].get("rpcRequestId") == event["rpcRequestId"]
    ]
    receipts = {
        (event.get("rpcRequestId"), event.get("sessionId"), event.get("threadId"))
        for event in events
        if event["type"] == "approval.server_resolved"
        and event.get("rpcRequestId") is not None
    }
    server_resolved = [
        event for event in wire_resolved
        if (event["rpcRequestId"], event.get("sessionId"), event.get("threadId")) in receipts
    ]
    command_statuses = {
        (event.get("sessionId"), event.get("threadId"),
         event.get("turnId"), event.get("itemId")): event.get("itemStatus")
        for event in events if event["type"] == "approval.command_completed"
    }

    def command_status(event):
        request = requests[event["approvalId"]]["request"]
        return command_statuses.get((
            event.get("sessionId"), event.get("threadId"),
            request.get("turnId"), request.get("itemId"),
        ))

    accepted = [
        event for event in server_resolved
        if event.get("response", {}).get("decision") == "accept"
        and requests.get(event["approvalId"], {}).get("request", {}).get("command") == allow_command
        and command_status(event) in {"completed", "failed"}
    ]
    denied = [
        event for event in server_resolved
        if event.get("response", {}).get("decision") == "decline"
        and requests.get(event["approvalId"], {}).get("request", {}).get("command") == deny_command
        and command_status(event) == "declined"
    ]
    return {
        "approvalCount": len(requests),
        "resolvedCount": len(resolved),
        "wireSentCount": len(wire_resolved),
        "serverResolvedCount": len(server_resolved),
        "approvalSessions": sorted({event["sessionId"] for event in requests.values()}),
        "approvedExactCommand": bool(accepted),
        "deniedExactCommand": bool(denied),
    }


def strict_outcomes_satisfied(outcomes, first_session, second_session):
    return (
        outcomes["approvedExactCommand"] and outcomes["deniedExactCommand"]
        and outcomes["approvalCount"] == outcomes["resolvedCount"]
        and outcomes["approvalCount"] == outcomes["wireSentCount"]
        and outcomes["approvalCount"] == outcomes["serverResolvedCount"]
        and set(outcomes["approvalSessions"]) == {first_session, second_session}
    )


def approval_diagnostics(events, allow_command, deny_command, session_ids,
                         *, show_commands=False):
    """Explain a failed gate without printing operator-supplied command text."""
    valid_sessions = set(session_ids)
    resolved = {
        event.get("approvalId"): event
        for event in events if event["type"] == "approval.resolved"
    }
    statuses = {
        (event.get("sessionId"), event.get("threadId"),
         event.get("turnId"), event.get("itemId")): event.get("itemStatus")
        for event in events if event["type"] == "approval.command_completed"
    }
    result = []
    for event in events:
        if event["type"] != "approval.requested" or event.get("sessionId") not in valid_sessions:
            continue
        request = event.get("request", {})
        decision = resolved.get(event.get("approvalId"), {})
        diagnostic = {
            "sessionId": event["sessionId"],
            "matchesAllowedCommand": request.get("command") == allow_command,
            "matchesDeniedCommand": request.get("command") == deny_command,
            "acceptOffered": (
                None if request.get("availableDecisions") is None
                else "accept" in request["availableDecisions"]
            ),
            "additionalPermissionsRequested": bool(request.get("additionalPermissions")),
            "verdict": decision.get("verdict"),
            "reason": decision.get("reason"),
            "responseDecision": decision.get("response", {}).get("decision"),
            "commandStatus": statuses.get((
                event.get("sessionId"), event.get("threadId"),
                request.get("turnId"), request.get("itemId"),
            )),
        }
        if show_commands:
            diagnostic["requestedCommand"] = request.get("command")
        result.append(diagnostic)
    return result


async def run(args):
    if args.require_outcomes and (not args.allow_command or not args.deny_command):
        raise ValueError("strict gate requires --allow-command and --deny-command")
    if args.require_outcomes and args.allow_command == args.deny_command:
        raise ValueError("strict gate commands must be distinct")
    if args.timeout <= 0:
        raise ValueError("timeout must be positive")
    first_project = args.first.expanduser().resolve(strict=True)
    second_project = args.second.expanduser().resolve(strict=True)
    if (
        first_project == second_project
        or first_project in second_project.parents
        or second_project in first_project.parents
    ):
        raise ValueError("the two worker projects must be unrelated directories")
    config = OperatorConfig.load(path=args.config)
    observed = []
    last_sequence = 0
    judge = ExampleJudge(args.allow_command, args.deny_command)
    async with await Coordinator.connect(config, judge) as coordinator:
        async def watch():
            nonlocal last_sequence
            async for event in coordinator.events(after=0):
                if event.type in {
                    "approval.requested", "approval.resolved", "approval.wire_sent",
                    "approval.server_resolved", "approval.command_completed",
                }:
                    observed.append(dict(event.data))
                last_sequence = event.sequence

        watcher = asyncio.create_task(watch())
        try:
            async def workflow():
                first, second = await asyncio.gather(
                    coordinator.start(str(args.first), args.first_goal),
                    coordinator.start(str(args.second), args.second_goal),
                )
                initial = await asyncio.gather(first.wait(), second.wait())
                if any(result.state != "completed" for result in initial):
                    raise RuntimeError(f"worker turn did not complete: {[r.state for r in initial]}")
                await first.follow_up(args.follow_up)
                follow_up = await first.wait()
                if follow_up.state != "completed":
                    raise RuntimeError(f"follow-up did not complete: {follow_up.state}")
                return first, second, follow_up

            first, second, follow_up = await asyncio.wait_for(workflow(), args.timeout)
            try:
                async with asyncio.timeout(5):
                    while True:
                        if watcher.done():
                            await watcher
                            raise RuntimeError("event watcher stopped before outcome verification")
                        target_sequence = coordinator.event_cursor
                        if last_sequence >= target_sequence:
                            outcomes = summarize_outcomes(
                                observed, args.allow_command, args.deny_command,
                                session_ids=(first.id, second.id),
                            )
                            if not args.require_outcomes or strict_outcomes_satisfied(
                                outcomes, first.id, second.id
                            ):
                                break
                        await asyncio.sleep(0.01)
            except TimeoutError:
                outcomes = summarize_outcomes(
                    observed, args.allow_command, args.deny_command,
                    session_ids=(first.id, second.id),
                )
            report = {
                "sessions": {"first": first.id, "second": second.id},
                "followUpState": follow_up.state,
                **outcomes,
            }
            if args.require_outcomes and not strict_outcomes_satisfied(
                outcomes, first.id, second.id
            ):
                report["approvalDiagnostics"] = approval_diagnostics(
                    observed, args.allow_command, args.deny_command,
                    (first.id, second.id),
                    show_commands=args.show_approval_commands,
                )
            print(json.dumps(report, sort_keys=True))
            if args.require_outcomes and not strict_outcomes_satisfied(outcomes, first.id, second.id):
                raise RuntimeError("generic live gate did not observe both exact approval outcomes")
            return report
        finally:
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--first", type=Path, required=True)
    parser.add_argument("--second", type=Path, required=True)
    parser.add_argument("--first-goal", required=True)
    parser.add_argument("--second-goal", required=True)
    parser.add_argument("--follow-up", required=True)
    parser.add_argument("--allow-command", help="only this exact command may be approved once")
    parser.add_argument("--deny-command", help="exact command whose denial is required by the gate")
    parser.add_argument("--require-outcomes", action="store_true")
    parser.add_argument("--show-approval-commands", action="store_true",
                        help="include requested command text in failed-gate diagnostics; may expose sensitive data")
    parser.add_argument("--timeout", type=float, default=1800)
    asyncio.run(run(parser.parse_args()))
