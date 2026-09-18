"""Exercise the installed transparent coordinator without checkout imports."""

import argparse
import asyncio
import tempfile
from pathlib import Path

from codex_coordinator import Coordinator
from codex_coordinator.service import AutomaticApprovalHandler, CoordinatorService, EventLog


def require(condition: bool, message: str) -> None:
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
            return {"thread": {"id": f"thread-{len(self.calls)}"}}
        if method == "turn/start":
            return {"turn": {"id": f"turn-{len(self.calls)}"}}
        return {}

    async def close(self):
        self.disconnected.set()


def approval(thread_id: str) -> dict:
    return {"id": 1, "method": "item/commandExecution/requestApproval", "params": {
        "threadId": thread_id, "turnId": "turn-1", "itemId": "item-1",
        "startedAtMs": 1, "availableDecisions": ["accept", "acceptForSession", "decline"],
    }}


async def run(first: Path, second: Path) -> None:
    projects = {"alpha": first.resolve(strict=True), "beta": second.resolve(strict=True)}
    events, client = EventLog(), FixtureClient()
    approvals = AutomaticApprovalHandler(events)
    service = CoordinatorService(client, approvals, events, projects=projects)
    coordinator = Coordinator(service, approvals, events, client)
    try:
        alpha, beta = await asyncio.gather(
            coordinator.start("alpha", "alpha task"), coordinator.start("beta", "beta task")
        )
        require(alpha.project_path == str(projects["alpha"]), "project path was not exposed")
        response = await approvals(approval(alpha.thread_id))
        require(response == {"decision": "acceptForSession"}, "request was not auto-approved")
        thread_params = [params for method, params in client.calls if method == "thread/start"]
        require(all(set(params) == {"cwd"} for params in thread_params), "thread/start leaked security settings")
        for handle in (alpha, beta):
            await service.notification({"method": "turn/completed", "params": {
                "threadId": handle.thread_id,
                "turn": {"id": service.sessions[handle.id].turn_id, "status": "completed"},
            }})
        await alpha.follow_up("follow-up", effort="high")
        require(client.calls[-1][1]["threadId"] == alpha.thread_id, "follow-up targeted wrong child")
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
            first, second = root / "alpha", root / "beta"
            first.mkdir(); second.mkdir()
            asyncio.run(run(first, second))


if __name__ == "__main__":
    main()
