"""Exercise the installed coordinator without test or checkout imports."""

import asyncio
import argparse
import tempfile
from pathlib import Path

from codex_coordinator import ApprovalRequest, CoordinationEvent, Coordinator, JudgeDecision
from codex_coordinator.coordinator import ApprovalPolicy, WorkerPermissions
from codex_coordinator.service import ApprovalBroker, CoordinatorService, EventLog


def require(condition: bool, message: str) -> None:
    """Keep release-gate checks active even under ``python -O``."""
    if not condition:
        raise RuntimeError(f"installed fixture: {message}")


class FixtureClient:
    def __init__(self):
        self.calls = []
        self.disconnected = asyncio.Event()
        self.connection_error = None

    async def call(self, method, params):
        self.calls.append((method, params))
        if method == "thread/start":
            require("historyMode" not in params, "paginated thread creation is unsupported")
            return {"thread": {"id": f"thread-{len(self.calls)}"}}
        if method == "turn/start":
            return {"turn": {"id": f"turn-{len(self.calls)}"}}
        return {}

    async def close(self):
        self.disconnected.set()


class FixtureJudge:
    async def decide(self, case):
        if case.request.get("command") == "git status --short":
            return JudgeDecision("approve_once", "fixture permits exact status command")
        return JudgeDecision("deny", "fixture denies other commands")


def project(root: Path, name: str) -> Path:
    """A worker project holds no Codex configuration; the operator owns it."""
    path = root / name
    path.mkdir(parents=True)
    return path


def approval(thread_id: str, command: str) -> dict:
    return {
        "method": ApprovalPolicy.COMMAND,
        "params": {
            "threadId": thread_id, "turnId": "turn-1", "itemId": "item-1",
            "startedAtMs": 1, "command": command, "cwd": ".",
            "availableDecisions": ["accept", "decline"],
        },
    }


async def run(first: Path, second: Path) -> None:
    first = first.expanduser().resolve(strict=True)
    second = second.expanduser().resolve(strict=True)
    if not first.is_dir() or not second.is_dir() or first == second or first in second.parents or second in first.parents:
        raise ValueError("installed fixture needs two unrelated project directories")
    events = EventLog()
    broker = ApprovalBroker(events, judge=FixtureJudge())
    client = FixtureClient()
    service = CoordinatorService(
        client, broker, events, allowed_roots=(first, second),
        worker_permissions={
            first: WorkerPermissions(sandbox_mode="workspace-write", source="installed fixture"),
            second: WorkerPermissions(sandbox_mode="workspace-write", source="installed fixture"),
        },
    )
    coordinator = Coordinator(service, broker, events, client)
    try:
        alpha, beta = await asyncio.gather(
            coordinator.start(str(first), "generic alpha task"),
            coordinator.start(str(second), "generic beta task"),
        )
        require(alpha.id != beta.id, "worker session IDs are not distinct")
        responses = await asyncio.gather(
            broker(approval(alpha.thread_id, "git status --short")),
            broker(approval(beta.thread_id, "risky command")),
        )
        require(
            responses == [{"decision": "accept"}, {"decision": "decline"}],
            "approval and denial were not both enforced",
        )
        typed_approvals = [
            CoordinationEvent.from_record(event).approval
            for event in events.events if event["type"] == "approval.requested"
        ]
        require(len(typed_approvals) == 2, "two approval events were not recorded")
        require(
            all(isinstance(item, ApprovalRequest) for item in typed_approvals),
            "approval events are not typed",
        )
        require(
            {item.session_id for item in typed_approvals} == {alpha.id, beta.id},
            "approval events are not correlated to both sessions",
        )
        for handle in (alpha, beta):
            await service.notification({
                "method": "turn/completed", "params": {
                    "threadId": handle.thread_id, "turn": {"id": service.sessions[handle.id].turn_id, "status": "completed"},
                },
            })
        require((await alpha.wait(timeout=1)).state == "completed", "first turn did not complete")
        require((await beta.wait(timeout=1)).state == "completed", "second turn did not complete")
        await alpha.follow_up("generic follow-up")
        require(
            client.calls[-1][1]["threadId"] == alpha.thread_id,
            "follow-up targeted the wrong worker",
        )
        await service.notification({
            "method": "turn/completed", "params": {
                "threadId": alpha.thread_id, "turn": {"id": service.sessions[alpha.id].turn_id, "status": "completed"},
            },
        })
        require((await alpha.wait(timeout=1)).state == "completed", "follow-up did not complete")
    finally:
        await coordinator.close()
    print("installed two-project fixture passed")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--first", type=Path)
    parser.add_argument("--second", type=Path)
    args = parser.parse_args()
    if (args.first is None) != (args.second is None):
        parser.error("--first and --second must be supplied together")
    if args.first is not None:
        asyncio.run(run(args.first, args.second))
    else:
        with tempfile.TemporaryDirectory(prefix="coordinator-installed-") as directory:
            root = Path(directory)
            asyncio.run(run(project(root, "alpha"), project(root, "beta")))


if __name__ == "__main__":
    main()
