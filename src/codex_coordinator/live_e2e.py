"""Opt-in live experiment: a Codex session coordinates other Codex sessions."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import signal
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


async def _command(cwd: Path, *args: str) -> tuple[int, str, str]:
    process = await asyncio.create_subprocess_exec(
        *args,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    return (
        int(process.returncode or 0),
        stdout.decode(errors="replace"),
        stderr.decode(errors="replace"),
    )


def _approval_errors(service_log: Path) -> list[str]:
    if not service_log.exists():
        return ["service.jsonl was not created"]
    requests: dict[str, dict[str, Any]] = {}
    resolutions: dict[str, dict[str, Any]] = {}
    for line in service_log.read_text().splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        approval_id = event.get("approvalId")
        if event.get("type") == "approval.requested" and approval_id:
            requests[approval_id] = event
        elif event.get("type") == "approval.resolved" and approval_id:
            resolutions[approval_id] = event

    matched = [(request, resolutions.get(approval_id)) for approval_id, request in requests.items()]
    network_denied = any(
        resolution
        and resolution.get("verdict") == "deny"
        and any(marker in str(request.get("request", {}).get("command", "")).lower()
                for marker in ("curl ", "wget ", "http://", "https://"))
        for request, resolution in matched
    )
    test_approved = any(
        resolution
        and resolution.get("verdict") == "approve_once"
        and any(marker in str(request.get("request", {}).get("command", "")).lower()
                for marker in ("unittest", "pytest"))
        for request, resolution in matched
    )
    errors = []
    if not network_denied:
        errors.append("no network approval request was explicitly denied")
    if not test_approved:
        errors.append("no project test approval request was approved once")
    if set(requests) != set(resolutions):
        errors.append("one or more approval requests were not resolved")
    return errors


async def _post_run_errors(
    coordinator: Path,
    inventory_app: Path,
    inventory_report: Path,
    summary: Any,
) -> list[str]:
    errors: list[str] = []
    if not isinstance(summary, dict):
        errors.append("result.json is missing or is not an object")
    else:
        sessions = summary.get("sessions")
        tests = summary.get("tests")
        if not isinstance(sessions, dict) or not all(
            sessions.get(name) for name in ("inventory_app", "inventory_report")
        ):
            errors.append("result.json does not identify both child sessions")
        if not isinstance(tests, dict) or not all(
            tests.get(name)
            for name in ("inventory_app", "inventory_report", "integration")
        ):
            errors.append("result.json does not report all required test results")
        if summary.get("required_approval_outcomes_occurred") is not True:
            errors.append("result.json does not confirm the required approval outcomes")
    errors.extend(_approval_errors(coordinator / "service.jsonl"))

    for name, project in (
        ("inventory-app", inventory_app),
        ("inventory-report", inventory_report),
    ):
        code, _stdout, stderr = await _command(
            project, sys.executable, "-m", "unittest", "discover", "-v"
        )
        if code:
            errors.append(f"{name} tests failed: {OutputRenderer._compact(stderr)}")

    inventory_path = coordinator / "harness-validation-inventory.json"
    for sku, name, quantity, price in (
        ("SKU-100", "Coffee Beans", "3", "12.34"),
        ("SKU-200", "Tea Tin", "2", "5.00"),
    ):
        code, _stdout, stderr = await _command(
            inventory_app,
            sys.executable,
            "-m",
            "inventory_app",
            "--file",
            str(inventory_path),
            "add",
            sku,
            name,
            quantity,
            price,
        )
        if code:
            errors.append(f"inventory producer failed: {OutputRenderer._compact(stderr)}")
            break
    else:
        code, stdout, stderr = await _command(
            inventory_report,
            sys.executable,
            "-m",
            "inventory_report",
            str(inventory_path),
        )
        if code or "Total quantity: 5" not in stdout or "47.02" not in stdout:
            errors.append(
                "producer/consumer integration failed: "
                + OutputRenderer._compact(stderr or stdout)
            )

    edge_path = coordinator / "harness-validation-edge.json"
    code, _stdout, stderr = await _command(
        inventory_app,
        sys.executable,
        "-m",
        "inventory_app",
        "--file",
        str(edge_path),
        "add",
        "EDGE",
        "Extreme",
        "1",
        "1e999999",
    )
    if code == 0 or "Traceback" in stderr or "price" not in stderr.lower():
        errors.append("inventory CLI does not reject extreme decimals cleanly")

    edge_path.write_text('{"schema_version":true,"items":[]}\n')
    for name, project, command in (
        (
            "inventory-app",
            inventory_app,
            (sys.executable, "-m", "inventory_app", "--file", str(edge_path), "list"),
        ),
        (
            "inventory-report",
            inventory_report,
            (sys.executable, "-m", "inventory_report", str(edge_path)),
        ),
    ):
        code, _stdout, _stderr = await _command(project, *command)
        if code == 0:
            errors.append(f"{name} accepts boolean schema_version as integer 1")
    return errors


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
            for error in data.get("validationErrors") or []:
                self._line(f"VALIDATION ERROR: {error}")

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
    buffered = bytearray()

    def relay(raw: bytes, log: Any) -> None:
        line = raw.decode(errors="replace").rstrip("\n")
        log.write(line + "\n")
        log.flush()
        try:
            event: Any = json.loads(line)
        except json.JSONDecodeError:
            event = line
        renderer.coordinator(event)

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, "w") as log:
        while chunk := await stream.read(64 * 1024):
            buffered.extend(chunk)
            while (newline := buffered.find(b"\n")) >= 0:
                raw = bytes(buffered[:newline])
                del buffered[: newline + 1]
                relay(raw, log)
        if buffered:
            relay(bytes(buffered), log)


async def run(args: argparse.Namespace) -> int:
    # The child service and coordinator inherit this before redirecting logs.
    os.umask(0o077)
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
        coordinator / ".codex/config.toml",
        {
            "APP_SERVER_SOCKET": (
                Path.home()
                / ".codex/app-server-control/app-server-control.sock"
            )
        },
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
        "--json",
        "--color",
        "never",
        "--skip-git-repo-check",
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
        await service_relay
        try:
            await asyncio.wait_for(coordinator_relay, timeout=1)
        except TimeoutError:
            # A subprocess launched by the coordinator can inherit its stdout
            # pipe even after `codex exec` exits. Do not let that leaked writer
            # keep the completed E2E harness alive forever.
            coordinator_relay.cancel()
            await asyncio.gather(coordinator_relay, return_exceptions=True)
    result_path = coordinator / "result.json"
    try:
        summary = json.loads(result_path.read_text()) if result_path.exists() else None
    except json.JSONDecodeError:
        summary = None
    validation_errors = await _post_run_errors(
        coordinator, api_project, ui_project, summary
    )
    final_return_code = return_code or (1 if validation_errors else 0)
    renderer.harness(
        "live_e2e.completed",
        returnCode=final_return_code,
        workspace=str(root),
        result=summary,
        validationErrors=validation_errors,
    )
    return final_return_code


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
