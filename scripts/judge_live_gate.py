"""Operator-run live check of the one-shot Codex judge's restricted profile.

Run outside a nested Codex sandbox with a signed-in supported Codex CLI.
The runner probes the profile's denied-read boundary before invoking Codex.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from codex_coordinator.coordinator import (
    ApprovalCase,
    OneShotCodexJudge,
    codex_exec_json_runner,
)


async def main() -> None:
    runner_error: Exception | None = None

    async def run(prompt: str) -> str:
        nonlocal runner_error
        try:
            return await codex_exec_json_runner(prompt)
        except Exception as exc:
            runner_error = exc
            raise

    case = ApprovalCase(
        method="item/commandExecution/requestApproval",
        thread_id="judge-live-gate",
        project=str(Path.cwd().resolve()),
        request={"command": "false"},
        enforced_capabilities={"filesystemWriteRoots": [], "networkAccess": False},
    )
    judge = OneShotCodexJudge(
        run, policy_instructions="For this smoke check, deny every request.",
    )
    decision = await judge.decide(case)
    if runner_error is not None:
        raise RuntimeError(f"live judge runner failed: {runner_error}") from runner_error
    if decision.reason == "judge returned an invalid response":
        raise RuntimeError("live judge did not return a valid structured decision")
    if decision.verdict != "deny":
        raise RuntimeError(f"live judge did not obey the deny-all policy: {decision.verdict}")
    print(json.dumps({"liveJudgeGate": "passed", "verdict": decision.verdict,
                      "profile": "coordinator_judge"}, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
