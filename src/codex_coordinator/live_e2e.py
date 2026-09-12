"""Opt-in live experiment: a Codex session coordinates other Codex sessions."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import signal
import tempfile
from pathlib import Path


def _make_project(root: Path, examples: Path, name: str) -> Path:
    project = root / name
    if project.exists():
        raise FileExistsError(f"live E2E project already exists: {project}")
    shutil.copytree(examples / name, project)
    return project


def _resolve_template(path: Path, replacements: dict[str, Path]) -> str:
    rendered = path.read_text()
    for name, value in replacements.items():
        rendered = rendered.replace("{{" + name + "}}", str(value))
    if "{{" in rendered or "}}" in rendered:
        raise ValueError(f"unresolved template marker in {path}")
    path.write_text(rendered)
    return rendered


async def _relay_service_events(path: Path, stop: asyncio.Event) -> None:
    """Mirror the service JSONL log to the harness stdout as it grows."""
    offset = 0
    while True:
        if path.exists():
            with path.open() as stream:
                stream.seek(offset)
                while line := stream.readline():
                    offset = stream.tell()
                    line = line.rstrip("\n")
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        event = {"raw": line}
                    print(json.dumps({
                        "type": "live_e2e.service_event",
                        "event": event,
                    }, sort_keys=True), flush=True)
        if stop.is_set():
            return
        await asyncio.sleep(0.1)


async def run(args: argparse.Namespace) -> int:
    repo = Path(__file__).resolve().parents[2]
    if args.workspace:
        root = args.workspace.expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
    else:
        root = Path(tempfile.mkdtemp(prefix="codex-orchestration-e2e-"))
    examples = repo / "examples"
    coordinator = _make_project(root, examples, "coordinator")
    api_project = _make_project(root, examples, "inventory-app")
    ui_project = _make_project(root, examples, "inventory-report")
    _resolve_template(
        coordinator / "goals/inventory-report.md",
        {"INVENTORY_APP_PATH": api_project},
    )
    _resolve_template(
        coordinator / "goal.md",
        {
            "REPO_PATH": repo,
            "COORDINATOR_PATH": coordinator,
            "INVENTORY_APP_PATH": api_project,
            "INVENTORY_REPORT_PATH": ui_project,
        },
    )
    command = [
        args.codex_command,
        "exec",
        "--model",
        args.coordinator_model,
        "--config",
        f'model_reasoning_effort="{args.coordinator_reasoning_effort}"',
        "--dangerously-bypass-approvals-and-sandbox",
        "--json",
        "--color",
        "never",
        "--cd",
        str(coordinator),
        "--output-last-message",
        str(coordinator / "coordinator-final.txt"),
        "Read goal.md and complete every requirement in it.",
    ]
    print(json.dumps({"type": "live_e2e.started", "workspace": str(root)}), flush=True)
    process = await asyncio.create_subprocess_exec(*command, start_new_session=True)
    relay_stop = asyncio.Event()
    relay = asyncio.create_task(
        _relay_service_events(coordinator / "service.jsonl", relay_stop)
    )
    try:
        # Deliberately no timeout: this is the experiment's long-running coordinator.
        return_code = await process.wait()
    finally:
        if process.returncode is None:
            os.killpg(process.pid, signal.SIGTERM)
            await process.wait()
        relay_stop.set()
        await relay
    result_path = coordinator / "result.json"
    summary = json.loads(result_path.read_text()) if result_path.exists() else None
    print(json.dumps({
        "type": "live_e2e.completed",
        "returnCode": return_code,
        "workspace": str(root),
        "result": summary,
    }, sort_keys=True), flush=True)
    return return_code if return_code else (0 if summary else 1)


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--codex-command", default="codex")
    parser.add_argument("--coordinator-model", default="gpt-5.6-sol")
    parser.add_argument("--coordinator-reasoning-effort", default="medium")
    return parser.parse_args()


def main() -> None:
    raise SystemExit(asyncio.run(run(arguments())))


if __name__ == "__main__":
    main()
