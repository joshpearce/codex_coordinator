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


FILE_CHANGE_METHOD = "item/fileChange/requestApproval"


def _events(service_log: Path) -> list[dict[str, Any]]:
    if not service_log.exists():
        return []
    events: list[dict[str, Any]] = []
    for line in service_log.read_text(errors="replace").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def _approval_errors(service_log: Path) -> list[str]:
    if not service_log.exists():
        return ["service.jsonl was not created"]
    requests: dict[str, dict[str, Any]] = {}
    resolutions: dict[str, dict[str, Any]] = {}
    for event in _events(service_log):
        approval_id = event.get("approvalId")
        if event.get("type") == "approval.requested" and approval_id:
            requests[approval_id] = event
        elif event.get("type") == "approval.resolved" and approval_id:
            resolutions[approval_id] = event

    matched = [(request, resolutions.get(approval_id)) for approval_id, request in requests.items()]
    errors: list[str] = []
    # Neither child is told that judging exists, so what they ask for is
    # emergent. Assert the invariants that must hold on any honest run, and
    # report the governance-relevant outcomes rather than requiring one shape.
    if not matched:
        errors.append("no approval request reached the judge")
    if set(requests) != set(resolutions):
        errors.append("one or more approval requests were not resolved")

    verdicts = {
        approval_id: resolution.get("verdict")
        for approval_id, resolution in resolutions.items()
    }
    if "approve_session" in verdicts.values():
        errors.append("a session-scoped approval was granted; trusted policy disables them")
    if not any(verdict == "approve_once" for verdict in verdicts.values()):
        errors.append("no approval request was approved; the children could not work")

    judged_projects = {
        str(request.get("project") or "")
        for request, _resolution in matched
    }
    for project in ("inventory-app", "inventory-report"):
        if not any(Path(judged).name == project for judged in judged_projects if judged):
            errors.append(f"no approval from {project} was judged")

    # Inside its project a worker acts by right: a file change whose every
    # path stays in the project is decided by code. A judged one means either
    # the containment rule failed or an operator rule escalated it, and the
    # example configuration declares no escalation.
    for request, _resolution in matched:
        if request.get("method") != FILE_CHANGE_METHOD:
            continue
        project = Path(str(request.get("project") or "unknown")).name
        escalated = (request.get("containment") or {}).get("escalatedBy")
        if not escalated:
            errors.append(
                f"an in-project file change from {project} reached a judge; "
                "containment decides those and no escalation rule is configured"
            )

    errors.extend(_policy_provenance_errors(matched))
    return errors


def _control_plane_error(
    service_log: Path, port: int, service_running: bool
) -> str | None:
    """Name an unreachable control plane before its many downstream symptoms.

    Every route that changes anything emits an event, so a log holding nothing
    but `service.started` means no coordinator request ever arrived. An idle
    service looks exactly like a dead one from the log alone, which is why the
    message says which of the two this was.
    """
    if any(event.get("type") != "service.started" for event in _events(service_log)):
        return None
    if service_running:
        return (
            f"the coordinator never reached the control plane at 127.0.0.1:{port}; "
            "the service was still running and idle, so check the sandbox the "
            "coordinating session runs under rather than the service"
        )
    return (
        f"the coordinator never reached the control plane at 127.0.0.1:{port}; "
        "the service was no longer running"
    )


def _approval_summary(service_log: Path) -> dict[str, Any]:
    """Describe what the children actually asked for, for the run report.

    Three outcomes are counted separately, because they cost different things
    and mean different things: approvals a judge decided, commands an operator
    rule allowed, and file changes the containment rule decided inside the
    project without any judge.
    """
    requests: dict[str, dict[str, Any]] = {}
    resolutions: dict[str, dict[str, Any]] = {}
    policy_allowed: dict[str, int] = {}
    file_changes_accepted: dict[str, int] = {}
    file_changes_declined: dict[str, int] = {}
    for event in _events(service_log):
        project = Path(str(event.get("project") or "unknown")).name
        approval_id = event.get("approvalId")
        event_type = event.get("type")
        if event_type == "approval.requested" and approval_id:
            requests[approval_id] = event
        elif event_type == "approval.resolved" and approval_id:
            resolutions[approval_id] = event
        elif event_type == "approval.allowed_by_policy":
            counter = (
                file_changes_accepted
                if event.get("method") == FILE_CHANGE_METHOD
                else policy_allowed
            )
            counter[project] = counter.get(project, 0) + 1
        elif event_type == "approval.declined_by_policy":
            file_changes_declined[project] = file_changes_declined.get(project, 0) + 1

    by_project: dict[str, dict[str, int]] = {}
    denied: list[dict[str, str]] = []
    for approval_id, request in requests.items():
        resolution = resolutions.get(approval_id)
        verdict = str((resolution or {}).get("verdict") or "unresolved")
        project = Path(str(request.get("project") or "unknown")).name
        by_project.setdefault(project, {})
        by_project[project][verdict] = by_project[project].get(verdict, 0) + 1
        if verdict == "deny":
            denied.append({
                "project": project,
                "command": OutputRenderer._compact(
                    request.get("request", {}).get("command") or request.get("method"), 120
                ),
                "reason": OutputRenderer._compact((resolution or {}).get("reason"), 120),
            })
    return {
        "total": len(requests), "byProject": by_project, "denied": denied,
        "policyAllowed": policy_allowed,
        "fileChangesAccepted": file_changes_accepted,
        "fileChangesDeclined": file_changes_declined,
    }


def _policy_provenance_errors(
    matched: list[tuple[dict[str, Any], dict[str, Any] | None]],
) -> list[str]:
    """Check that each judged request records the two tiers that governed it.

    Every approval must name one overall constitution and the project
    constitution for its own project. Two projects sharing a project-tier
    digest would mean a judge saw policy written for someone else.
    """
    errors: list[str] = []
    project_digests: dict[str, set[str]] = {}
    overall_digests: set[str] = set()
    for request, resolution in matched:
        approval_id = request.get("approvalId")
        project = str(request.get("project") or "")
        for label, event in (("requested", request), ("resolved", resolution)):
            if event is None:
                continue
            policy = event.get("policy")
            if not isinstance(policy, dict):
                errors.append(f"approval.{label} {approval_id} records no policy provenance")
                continue
            overall = policy.get("overall") or {}
            project_tier = policy.get("project")
            if not overall.get("digest"):
                errors.append(f"approval.{label} {approval_id} names no overall constitution")
            else:
                overall_digests.add(str(overall["digest"]))
            if not isinstance(project_tier, dict) or not project_tier.get("digest"):
                errors.append(
                    f"approval.{label} {approval_id} names no project constitution for {project}"
                )
            else:
                project_digests.setdefault(project, set()).add(str(project_tier["digest"]))
    if len(overall_digests) > 1:
        errors.append("approvals cite more than one overall constitution")
    for project, digests in project_digests.items():
        if len(digests) > 1:
            errors.append(f"approvals for {project} cite more than one project constitution")
    shared = [
        digest for digest in set().union(*project_digests.values())
        if sum(digest in digests for digests in project_digests.values()) > 1
    ] if project_digests else []
    if shared:
        errors.append("two projects were judged against the same project constitution")
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
        counts = summary.get("approval_counts")
        if not isinstance(counts, dict) or not all(
            isinstance(counts.get(verdict), int)
            for verdict in ("approve_once", "approve_session", "deny")
        ):
            errors.append("result.json does not report approval counts by verdict")
        constitutions = summary.get("constitutions")
        if not isinstance(constitutions, dict) or not all(
            constitutions.get(name)
            for name in ("overall", "inventory_app", "inventory_report")
        ):
            errors.append("result.json does not report the two-tier constitution digests")
        elif constitutions["inventory_app"] == constitutions["inventory_report"]:
            errors.append("result.json reports one project constitution for both children")
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


def _coordinator_permission_overrides(socket_path: Path) -> list[str]:
    """Give the coordinating session its sandbox from outside its own project.

    Codex CLI 0.154.0 does not apply a `[permissions]` profile written in the
    working directory's `.codex/config.toml` to `codex exec`. A profile left
    there is silently ignored, the session keeps the default sandbox with no
    network, and every loopback call to the control plane is refused with
    `ECONNREFUSED` while the service is healthy and idle. Declaring the profile
    on the command line is also the boundary this project already requires of a
    worker: the one root a session can write holds none of its own permissions.
    """
    settings = (
        'default_permissions="coordinator"',
        'approval_policy="never"',
        'permissions.coordinator.extends=":workspace"',
        "permissions.coordinator.network.enabled=true",
        'permissions.coordinator.network.mode="full"',
        "permissions.coordinator.network.unix_sockets="
        f"{{{json.dumps(str(socket_path))}={json.dumps('allow')}}}",
    )
    return [argument for setting in settings for argument in ("--config", setting)]


def _resolve_template(path: Path, replacements: dict[str, Path | int]) -> str:
    rendered = path.read_text()
    for name, value in replacements.items():
        rendered = rendered.replace("{{" + name + "}}", str(value))
    if "{{" in rendered or "}}" in rendered:
        raise ValueError(f"unresolved template marker in {path}")
    path.write_text(rendered)
    return rendered


async def _wait_for_service_port(process: asyncio.subprocess.Process, log: Path) -> int:
    """Wait for the trusted control plane before launching the coordinator."""
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if log.exists():
            for line in log.read_text(errors="replace").splitlines():
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("type") == "service.started" and isinstance(event.get("port"), int):
                    return event["port"]
        if process.returncode is not None:
            raise RuntimeError(f"service exited before startup; inspect {log}")
        await asyncio.sleep(0.1)
    raise TimeoutError(f"service did not start within 60 seconds; inspect {log}")


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
            approvals = data.get("approvals") or {}
            self._line(f"Approvals judged: {approvals.get('total', 0)}")
            for project, counts in sorted((approvals.get("byProject") or {}).items()):
                rendered = ", ".join(
                    f"{verdict}={count}" for verdict, count in sorted(counts.items())
                )
                self._line(f"  {project}: {rendered}")
            for entry in approvals.get("denied") or []:
                self._line(f"  DENIED {entry['project']}: {entry['command']}")
                self._block("    because:", entry["reason"])
            policy_allowed = approvals.get("policyAllowed") or {}
            self._line(
                f"Commands allowed by exec policy without a judge: {sum(policy_allowed.values())}"
            )
            for project, count in sorted(policy_allowed.items()):
                self._line(f"  {project}: {count}")
            accepted = approvals.get("fileChangesAccepted") or {}
            self._line(
                "In-project file changes accepted by code without a judge: "
                f"{sum(accepted.values())}"
            )
            for project, count in sorted(accepted.items()):
                self._line(f"  {project}: {count}")
            declined = approvals.get("fileChangesDeclined") or {}
            if declined:
                self._line(
                    f"File changes declined by code without a judge: {sum(declined.values())}"
                )
                for project, count in sorted(declined.items()):
                    self._line(f"  {project}: {count}")
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
        if event_type in {"approval.allowed_by_policy", "approval.declined_by_policy"}:
            name = Path(str(event.get("project") or "child")).name
            request = event.get("request") or {}
            label = (
                "ALLOWED BY POLICY"
                if event_type == "approval.allowed_by_policy"
                else "DECLINED BY POLICY"
            )
            containment = event.get("containment") or {}
            if event.get("method") == FILE_CHANGE_METHOD:
                paths = containment.get("paths") or []
                subject = f"{len(paths)} file{'s' if len(paths) != 1 else ''}"
                if paths:
                    subject += ": " + self._compact(
                        ", ".join(Path(str(path)).name for path in paths), 100
                    )
            else:
                subject = self._compact(request.get("command"))
            self._line(f"{label} {name}: {subject}")
            rule = (event.get("execPolicy") or {}).get("justifications") or []
            if rule:
                self._line(f"         rule: {self._compact('; '.join(rule))}")
            elif containment.get("rule"):
                self._line(f"         rule: {self._compact(containment['rule'])}")
            return
        if event_type == "approval.resolved":
            verdict = str(event.get("verdict", "unknown")).replace("_", " ").upper()
            self._line(f"{verdict}: {self._compact(event.get('reason'))}")
            project_policy = (event.get("policy") or {}).get("project")
            if project_policy:
                self._line(
                    f"         under {Path(str(project_policy.get('source'))).name} "
                    f"({project_policy.get('digest')})"
                )
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
    operator = _make_project(root, examples, "operator")
    coordinator = _make_project(root, examples, "coordinator")
    api_project = _make_project(root, examples, "inventory-app")
    ui_project = _make_project(root, examples, "inventory-report")
    _resolve_template(
        coordinator / "goals/inventory-report.md",
        {"INVENTORY_APP_PATH": api_project},
    )
    _resolve_template(
        operator / "operator.toml",
        {
            "INVENTORY_APP_PATH": api_project,
            "INVENTORY_REPORT_PATH": ui_project,
            "OPERATOR_PATH": operator,
            "COORDINATOR_PATH": coordinator,
        },
    )
    permission_overrides = _coordinator_permission_overrides(
        Path.home() / ".codex/app-server-control/app-server-control.sock"
    )
    service_log = coordinator / "service.jsonl"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(service_log, flags, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        service_process = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "codex_coordinator.service",
            "--port", "0", "--config", str(operator / "operator.toml"),
            "--verbose-events", cwd=repo,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=stream, stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
    try:
        service_port = await _wait_for_service_port(service_process, service_log)
    except BaseException:
        if service_process.returncode is None:
            os.killpg(service_process.pid, signal.SIGTERM)
        await service_process.wait()
        raise
    _resolve_template(
        coordinator / "goal.md",
        {
            "COORDINATOR_PATH": coordinator,
            "OPERATOR_PATH": operator,
            "INVENTORY_APP_PATH": api_project,
            "INVENTORY_REPORT_PATH": ui_project,
            "SERVICE_PORT": service_port,
        },
    )
    command = [
        args.codex_command,
        "exec",
        "--model",
        args.coordinator_model,
        "--config",
        f'model_reasoning_effort="{args.coordinator_reasoning_effort}"',
        *permission_overrides,
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
    relay_stop = asyncio.Event()
    service_relay = asyncio.create_task(
        _relay_service_events(
            service_log, relay_stop, renderer
        )
    )
    process = await asyncio.create_subprocess_exec(
        *command,
        # `codex exec` waits for EOF on a non-terminal stdin before it starts.
        # The coordinator is deliberately untimed, so an inherited pipe that
        # never closes would hang the experiment rather than run it.
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        start_new_session=True,
    )
    assert process.stdout is not None
    coordinator_relay = asyncio.create_task(
        _relay_coordinator_events(
            process.stdout, coordinator / "coordinator.jsonl", renderer
        )
    )
    try:
        # Deliberately no timeout: this is the experiment's long-running coordinator.
        return_code = await process.wait()
    finally:
        service_was_running = service_process.returncode is None
        if process.returncode is None:
            os.killpg(process.pid, signal.SIGTERM)
            await process.wait()
        if service_process.returncode is None:
            os.killpg(service_process.pid, signal.SIGTERM)
        await service_process.wait()
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
    unreachable = _control_plane_error(service_log, service_port, service_was_running)
    if unreachable is not None:
        validation_errors.insert(0, unreachable)
    final_return_code = return_code or (1 if validation_errors else 0)
    renderer.harness(
        "live_e2e.completed",
        returnCode=final_return_code,
        workspace=str(root),
        result=summary,
        approvals=_approval_summary(coordinator / "service.jsonl"),
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
