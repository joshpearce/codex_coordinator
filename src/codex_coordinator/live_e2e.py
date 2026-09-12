"""Opt-in live experiment: a Codex session coordinates other Codex sessions."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import signal
import tempfile
import time
from pathlib import Path
from typing import Any


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


class OutputRenderer:
    """Render full JSON or a small human-readable orchestration timeline."""

    def __init__(self, *, verbose: bool = False, json_output: bool = False) -> None:
        self.verbose = verbose
        self.json_output = json_output
        self.started = time.monotonic()
        self.session_names: dict[str, str] = {}

    def _stamp(self) -> str:
        elapsed = int(time.monotonic() - self.started)
        return f"[{elapsed // 60:02d}:{elapsed % 60:02d}]"

    def _line(self, message: str) -> None:
        print(f"{self._stamp()} {message}", flush=True)

    def _block(self, label: str, value: Any) -> None:
        self._line(label)
        for line in str(value or "").splitlines() or [""]:
            print(f"         {line}", flush=True)

    def raw(self, source: str, event: Any) -> None:
        print(json.dumps({"source": source, "event": event}, sort_keys=True), flush=True)

    @staticmethod
    def _compact(value: Any, limit: int = 180) -> str:
        text = " ".join(str(value or "").split())
        return text if len(text) <= limit else text[: limit - 1] + "…"

    def harness(self, event_type: str, **data: Any) -> None:
        event = {"type": event_type, **data}
        if self.json_output:
            self.raw("harness", event)
            return
        if event_type == "live_e2e.started":
            self._line(f"Workspace: {data['workspace']}")
            self._line(
                f"Coordinator started ({data['model']}, {data['reasoningEffort']})"
            )
        elif event_type == "live_e2e.completed":
            outcome = "SUCCESS" if data.get("returnCode") == 0 and data.get("result") else "FAILED"
            self._line(f"Result: {outcome} (exit {data.get('returnCode')})")

    def service(self, event: dict[str, Any]) -> None:
        if self.json_output:
            self.raw("service", event)
            return
        event_type = event.get("type")
        if event_type == "service.started":
            self._line(f"Control service listening on {event.get('host')}:{event.get('port')}")
            return
        if event_type in {"session.started", "session.turn_started"}:
            session = event.get("session") or {}
            name = Path(str(session.get("project", "child"))).name
            self.session_names[str(session.get("id", ""))] = name
            action = "Started" if event_type == "session.started" else "Follow-up started for"
            self._line(f"{action} {name}")
            self._block("Prompt:", event.get("prompt"))
            return
        if event_type == "approval.requested":
            name = Path(str(event.get("project") or "child")).name
            request = event.get("request") or {}
            command = request.get("command") or event.get("method")
            self._line(f"APPROVAL REQUEST {name}: {self._compact(command)}")
            return
        if event_type == "approval.resolved":
            verdict = str(event.get("verdict", "unknown")).replace("_", " ").upper()
            self._line(f"{verdict}: {self._compact(event.get('reason'))}")
            return
        if event_type != "app_server.notification":
            return
        method = event.get("method")
        message = event.get("message") or {}
        params = message.get("params") or {}
        session_id = str(event.get("sessionId") or "")
        name = self.session_names.get(session_id, session_id or "child")
        if method == "turn/completed":
            turn = params.get("turn") or {}
            summary = ""
            for item in reversed(turn.get("items") or []):
                if item.get("type") == "agentMessage":
                    summary = self._compact(item.get("text"))
                    break
            suffix = f": {summary}" if summary else ""
            self._line(f"{name} completed ({turn.get('status', 'completed')}){suffix}")
        elif self.verbose and method == "item/started":
            item = params.get("item") or {}
            if item.get("type") == "commandExecution":
                self._line(f"{name} command: {self._compact(item.get('command'))}")
            elif item.get("type") == "fileChange":
                count = len(item.get("changes") or [])
                self._line(f"{name} changing {count} file{'s' if count != 1 else ''}")

    def coordinator(self, event: Any) -> None:
        if self.json_output:
            self.raw("coordinator", event)
            return
        if not isinstance(event, dict):
            if self.verbose:
                self._line(f"coordinator: {self._compact(event)}")
            return
        event_type = event.get("type")
        if event_type in {"error", "turn.failed"}:
            self._line(f"COORDINATOR ERROR: {self._compact(event)}")
        elif self.verbose and event_type == "item.completed":
            item = event.get("item") or {}
            if item.get("type") == "command_execution":
                self._line(
                    f"coordinator command (exit {item.get('exit_code')}): "
                    f"{self._compact(item.get('command'))}"
                )


async def _relay_service_events(
    path: Path, stop: asyncio.Event, renderer: OutputRenderer
) -> None:
    """Read the service JSONL log and render selected events as it grows."""
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
                    renderer.service(event)
        if stop.is_set():
            return
        await asyncio.sleep(0.1)


async def _relay_coordinator_events(
    stream: asyncio.StreamReader, path: Path, renderer: OutputRenderer
) -> None:
    """Persist the coordinator's complete JSONL output and render selected events."""
    with path.open("w") as log:
        while raw := await stream.readline():
            line = raw.decode(errors="replace").rstrip("\n")
            log.write(line + "\n")
            log.flush()
            try:
                event: Any = json.loads(line)
            except json.JSONDecodeError:
                event = line
            renderer.coordinator(event)


async def run(args: argparse.Namespace) -> int:
    renderer = OutputRenderer(verbose=args.verbose, json_output=args.json)
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
    renderer.harness(
        "live_e2e.started",
        workspace=str(root),
        model=args.coordinator_model,
        reasoningEffort=args.coordinator_reasoning_effort,
    )
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        start_new_session=True,
    )
    assert process.stdout is not None
    relay_stop = asyncio.Event()
    service_relay = asyncio.create_task(
        _relay_service_events(
            coordinator / "service.jsonl", relay_stop, renderer
        )
    )
    coordinator_relay = asyncio.create_task(
        _relay_coordinator_events(
            process.stdout, coordinator / "coordinator.jsonl", renderer
        )
    )
    try:
        # Deliberately no timeout: this is the experiment's long-running coordinator.
        return_code = await process.wait()
    finally:
        if process.returncode is None:
            os.killpg(process.pid, signal.SIGTERM)
            await process.wait()
        relay_stop.set()
        await asyncio.gather(service_relay, coordinator_relay)
    result_path = coordinator / "result.json"
    summary = json.loads(result_path.read_text()) if result_path.exists() else None
    renderer.harness(
        "live_e2e.completed",
        returnCode=return_code,
        workspace=str(root),
        result=summary,
    )
    return return_code if return_code else (0 if summary else 1)


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--codex-command", default="codex")
    parser.add_argument("--coordinator-model", default="gpt-5.6-sol")
    parser.add_argument("--coordinator-reasoning-effort", default="medium")
    output = parser.add_mutually_exclusive_group()
    output.add_argument("--verbose", action="store_true")
    output.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> None:
    raise SystemExit(asyncio.run(run(arguments())))


if __name__ == "__main__":
    main()
