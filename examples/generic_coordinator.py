"""Coordinate two configured child projects through the reusable Python API."""

import argparse
import asyncio
from pathlib import Path

from codex_coordinator import Coordinator, OperatorConfig


async def run(args) -> None:
    config = OperatorConfig.load(path=args.config)
    async with await Coordinator.connect(config) as coordinator:
        first, second = await asyncio.gather(
            coordinator.start(args.first, args.first_goal),
            coordinator.start(args.second, args.second_goal),
        )
        initial = await asyncio.gather(first.wait(timeout=args.timeout), second.wait(timeout=args.timeout))
        if any(result.state != "completed" for result in initial):
            raise RuntimeError(f"child turn did not complete: {[result.state for result in initial]}")
        await first.follow_up(args.follow_up, effort=args.follow_up_effort)
        result = await first.wait(timeout=args.timeout)
        if result.state != "completed":
            raise RuntimeError(f"follow-up did not complete: {result.state}")


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--first", required=True, help="first configured project name")
    parser.add_argument("--second", required=True, help="second configured project name")
    parser.add_argument("--first-goal", required=True)
    parser.add_argument("--second-goal", required=True)
    parser.add_argument("--follow-up", default="Review your work and run the relevant tests.")
    parser.add_argument("--follow-up-effort")
    parser.add_argument("--timeout", type=float, default=600)
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(run(arguments()))
