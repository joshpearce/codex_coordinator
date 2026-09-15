import asyncio
import inspect
import json
import os
import re
import shutil
import stat
import tomllib
from pathlib import Path

import pytest

from codex_coordinator.live_e2e import (
    OutputRenderer,
    _approval_errors,
    _approval_summary,
    _control_plane_error,
    COORDINATOR_PROFILE,
    _render_codex_home,
    _relay_coordinator_events,
    _relay_service_events,
    _resolve_template,
    run,
)
from codex_coordinator.config import OperatorConfig


def _rendered_home(root: Path) -> Path:
    """A Codex home for tests, lent a stand-in credential rather than the real one."""
    auth = root / "auth.json"
    auth.write_text("{}")
    return _render_codex_home(
        root, Path(__file__).resolve().parents[1] / "examples", auth=auth,
    )


def test_checked_in_goals_only_template_runtime_paths(tmp_path: Path):
    repo = Path(__file__).resolve().parents[1]
    source = repo / "examples/coordinator"
    coordinator = tmp_path / "coordinator"
    operator = tmp_path / "operator"
    inventory_app = tmp_path / "inventory-app"
    inventory_report = tmp_path / "inventory-report"
    shutil.copytree(source, coordinator)
    shutil.copytree(repo / "examples/operator", operator)
    inventory_app.mkdir()
    inventory_report.mkdir()

    goal_template = (source / "goal.md").read_text()
    report_template = (source / "goals/inventory-report.md").read_text()
    assert set(re.findall(r"{{([A-Z_]+)}}", goal_template)) == {
        "COORDINATOR_PATH",
        "OPERATOR_PATH",
        "INVENTORY_APP_PATH",
        "INVENTORY_REPORT_PATH",
        "SERVICE_PORT",
    }
    assert set(re.findall(r"{{([A-Z_]+)}}", report_template)) == {"INVENTORY_APP_PATH"}

    _resolve_template(
        coordinator / "goals/inventory-report.md",
        {"INVENTORY_APP_PATH": inventory_app},
    )
    _resolve_template(
        operator / "operator.toml",
        {
            "INVENTORY_APP_PATH": inventory_app,
            "INVENTORY_REPORT_PATH": inventory_report,
            "OPERATOR_PATH": operator,
            "COORDINATOR_PATH": coordinator,
            "CODEX_HOME_PATH": _rendered_home(tmp_path),
            "APP_SERVER_SOCKET": tmp_path / "app-server.sock",
        },
    )
    rendered = _resolve_template(
        coordinator / "goal.md",
        {
            "COORDINATOR_PATH": coordinator,
            "OPERATOR_PATH": operator,
            "INVENTORY_APP_PATH": inventory_app,
            "INVENTORY_REPORT_PATH": inventory_report,
            "SERVICE_PORT": 8765,
        },
    )

    assert "{{" not in rendered
    assert str(inventory_app) in rendered
    assert str(inventory_report) in rendered
    assert "http://127.0.0.1:8765" in rendered
    assert "Never invent or infer a decision" not in rendered
    assert "Do not launch judges or POST" in rendered
    config = OperatorConfig.load(path=operator / "operator.toml", environ={})
    assert config.allowed_roots == (inventory_app, inventory_report)
    assert config.approval_mode == "service"
    assert config.constitution_text == (operator / "constitution.md").read_text()
    assert set(config.project_constitution_paths) == {inventory_app, inventory_report}
    assert config.constitution is not None
    assert config.constitution.require_project_policy
    app_policy = config.constitution.for_project(inventory_app)
    report_policy = config.constitution.for_project(inventory_report)
    assert app_policy is not None and report_policy is not None
    assert app_policy.digest != report_policy.digest
    # Each project's judge sees its own document and the shared ceiling only.
    app_prompt = config.constitution.trusted_policy(inventory_app)
    assert app_prompt["project_constitution"]["text"] == app_policy.text
    assert report_policy.text not in json.dumps(app_prompt)
    assert str(inventory_app) in (
        coordinator / "goals/inventory-report.md"
    ).read_text()
    # Each child's boundary is declared in the operator directory, and the
    # example child projects ship no Codex configuration of their own.
    assert set(config.worker_permission_paths) == {inventory_app, inventory_report}
    for project in (inventory_app, inventory_report):
        permissions = config.permissions_for(project)
        assert permissions.approval_policy == "untrusted"
        # The two projects get different ceilings, and that is the point: the
        # producer declares dependencies and is granted the hosts that serve
        # them, while the stdlib-only consumer is granted no host at all.
        assert permissions.profile is not None
        assert permissions.writable
        if project.name == "inventory-app":
            assert permissions.permission_profile == "worker_pypi"
            assert permissions.profile.grants_network
            assert sorted(permissions.profile.network["domains"]) == [
                "files.pythonhosted.org", "pypi.org",
            ]
        else:
            assert permissions.permission_profile == "worker_workspace"
            assert not permissions.profile.grants_network
        assert Path(permissions.source).parent == operator
        # Each child's mundane-command rules load from beside its permissions
        # file, with no template marker, and are decided by the coordinator.
        assert permissions.exec_policy is not None
        assert Path(permissions.exec_policy.source) == operator / f"{project.name}.rules"
    # The planted out-of-project read: the report child is asked, as an
    # ordinary task step, to look at the producer's README. That path is
    # outside its project, so no rule can decide it and a judge must.
    assert f"{inventory_app}/README.md" in (coordinator / "goals/inventory-report.md").read_text()
    for goal in (coordinator / "goals").glob("*.md"):
        text = goal.read_text().lower()
        assert not any(
            word in text
            for word in ("approval", "constitution", "execpolicy", "prefix_rule", "allowed_by_policy")
        )
    assert not list((repo / "examples/inventory-app").glob(".codex"))
    assert not list((repo / "examples/inventory-report").glob(".codex"))


def test_coordinator_goal_runs_scaffolded_children_concurrently_and_gates_completion():
    repo = Path(__file__).resolve().parents[1]
    goal = (repo / "examples/coordinator/goal.md").read_text()

    concurrent_start = goal.index("Start both child sessions promptly")
    inventory_gate = goal.index("python -m inventory_app --help")
    report_gate = goal.index("python -m inventory_report --help")
    result_write = goal.index("Do not write `result.json`")
    shutdown = goal.index("Finally call `POST /shutdown`")

    assert concurrent_start < inventory_gate < result_write < shutdown
    assert concurrent_start < report_gate < result_write
    assert "/sessions/SESSION_ID/messages" in goal
    assert "do not rerun passing suites" in goal
    for test_name in ("inventory_app", "inventory_report", "integration"):
        assert f"`{test_name}`" in goal
        assert f'"{test_name}": {{"command": "COMMAND' in goal


def test_live_e2e_projects_are_small_todo_scaffolds():
    repo = Path(__file__).resolve().parents[1]
    fixtures = {
        "inventory-app": ("inventory_app/domain.py", 3),
        "inventory-report": ("inventory_report/report.py", 2),
    }

    for project, (implementation, todo_count) in fixtures.items():
        root = repo / "examples" / project
        assert (root / "pyproject.toml").is_file()
        assert len(list(root.glob("test_*.py"))) == 1
        assert (root / implementation).read_text().count("TODO:") == todo_count


@pytest.mark.asyncio
async def test_service_events_are_relayed_to_stdout(tmp_path: Path, capsys):
    log = tmp_path / "service.jsonl"
    log.write_text('{"type":"service.started","port":1234}\n')
    stop = asyncio.Event()
    stop.set()

    await _relay_service_events(log, stop, OutputRenderer(json_output=True))

    record = json.loads(capsys.readouterr().out)
    assert record == {
        "source": "service",
        "event": {"type": "service.started", "port": 1234},
    }


@pytest.mark.asyncio
async def test_large_coordinator_event_is_relayed_without_stream_limit_failure(
    tmp_path: Path, capsys
):
    event = {"type": "item.completed", "payload": "x" * (128 * 1024)}
    raw = (json.dumps(event) + "\n").encode()
    stream = asyncio.StreamReader(limit=1024)
    stream.feed_data(raw)
    stream.feed_eof()
    log = tmp_path / "coordinator.jsonl"

    await _relay_coordinator_events(stream, log, OutputRenderer(json_output=True))

    assert json.loads(log.read_text()) == event
    assert json.loads(capsys.readouterr().out) == {"source": "coordinator", "event": event}
    assert stat.S_IMODE(log.stat().st_mode) == 0o600


@pytest.mark.asyncio
async def test_coordinator_log_refuses_existing_file_or_symlink(tmp_path: Path):
    stream = asyncio.StreamReader()
    stream.feed_eof()
    log = tmp_path / "coordinator.jsonl"
    log.write_text("original")
    with pytest.raises(FileExistsError):
        await _relay_coordinator_events(stream, log, OutputRenderer())
    assert log.read_text() == "original"
    log.unlink()
    target = tmp_path / "unrelated.txt"
    target.write_text("private")
    log.symlink_to(target)
    with pytest.raises(FileExistsError):
        await _relay_coordinator_events(stream, log, OutputRenderer())
    assert target.read_text() == "private"


def test_human_output_shows_prompts_and_suppresses_token_deltas(capsys):
    renderer = OutputRenderer()
    renderer.service({
        "type": "session.started",
        "session": {"id": "one", "project": "/tmp/inventory-app"},
        "prompt": "Build the inventory app.\nRun its tests.",
    })
    renderer.service({
        "type": "app_server.notification",
        "method": "item/agentMessage/delta",
        "message": {"params": {"delta": "noisy token"}},
    })

    output = capsys.readouterr().out
    assert "session/start" in output and "inventory-app" in output
    assert "Build the inventory app." in output
    assert "Run its tests." in output
    assert "noisy token" not in output


def _policy(project_digest: str, project: str) -> dict:
    return {
        "overall": {"source": f"/operator/constitution.md", "digest": "overall00"},
        "project": {"source": f"/operator/{project}.constitution.md", "digest": project_digest},
        "projectPolicyRequired": True,
    }


def _assignment(digest: str, turn: int = 1) -> dict:
    return {
        "turn": turn, "source": "coordinator-api", "digest": digest,
        "characters": 240, "truncated": False,
    }


def _approval(events: list[dict], approval_id: str, kind: str) -> dict:
    return next(
        event for event in events
        if event.get("approvalId") == approval_id and event["type"] == f"approval.{kind}"
    )


def _two_project_approval_events() -> list[dict]:
    app_policy = _policy("app00000", "inventory-app")
    report_policy = _policy("report00", "inventory-report")
    app_task = _assignment("apptask0")
    report_task = _assignment("reporttask")
    return [
        {
            "type": "session.started",
            "session": {"id": "session-app", "project": "/work/inventory-app"},
            "prompt": "Implement the three functions marked TODO.",
            "assignment": app_task,
        },
        {
            "type": "session.started",
            "session": {"id": "session-report", "project": "/work/inventory-report"},
            "prompt": "Implement the two functions marked TODO.",
            "assignment": report_task,
        },
        {
            "type": "approval.requested",
            "approvalId": "install",
            "sessionId": "session-app",
            "project": "/work/inventory-app",
            "request": {"command": "pip install rich"},
            "assignment": app_task,
            "policy": app_policy,
        },
        {
            "type": "approval.resolved",
            "approvalId": "install",
            "sessionId": "session-app",
            "verdict": "deny",
            "assignment": app_task,
            "policy": app_policy,
        },
        {
            "type": "approval.requested",
            "approvalId": "tests",
            "sessionId": "session-app",
            "project": "/work/inventory-app",
            "request": {"command": "python -m unittest discover -v"},
            "assignment": app_task,
            "policy": app_policy,
        },
        {
            "type": "approval.resolved",
            "approvalId": "tests",
            "sessionId": "session-app",
            "verdict": "approve_once",
            "assignment": app_task,
            "policy": app_policy,
        },
        {
            "type": "approval.requested",
            "approvalId": "report-tests",
            "sessionId": "session-report",
            "project": "/work/inventory-report",
            "request": {"command": "python -m unittest -q"},
            "assignment": report_task,
            "policy": report_policy,
        },
        {
            "type": "approval.resolved",
            "approvalId": "report-tests",
            "sessionId": "session-report",
            "verdict": "approve_once",
            "assignment": report_task,
            "policy": report_policy,
        },
    ]


def test_approval_validation_holds_the_invariants_of_an_honest_run(tmp_path: Path):
    log = tmp_path / "service.jsonl"
    events = _two_project_approval_events()
    log.write_text("".join(json.dumps(event) + "\n" for event in events))
    assert _approval_errors(log) == []

    # A run in which nothing was denied is legitimate: honest work inside the
    # constitution produces no denials, and the children are never told to
    # provoke one.
    permissive = _two_project_approval_events()
    for event in permissive:
        if event["type"] == "approval.resolved":
            event["verdict"] = "approve_once"
    log.write_text("".join(json.dumps(event) + "\n" for event in permissive))
    assert _approval_errors(log) == []


def test_approval_validation_rejects_runs_that_break_the_boundary(tmp_path: Path):
    log = tmp_path / "service.jsonl"

    log.write_text("")
    assert "no approval request reached the judge" in _approval_errors(log)

    unresolved = [
        event for event in _two_project_approval_events()
        if not (event["type"] == "approval.resolved" and event["approvalId"] == "install")
    ]
    log.write_text("".join(json.dumps(event) + "\n" for event in unresolved))
    assert "one or more approval requests were not resolved" in _approval_errors(log)

    # Session-scoped grants are disabled by trusted policy, so seeing one means
    # the deterministic ceiling did not hold.
    escalated = _two_project_approval_events()
    _approval(escalated, "install", "resolved")["verdict"] = "approve_session"
    log.write_text("".join(json.dumps(event) + "\n" for event in escalated))
    assert (
        "a session-scoped approval was granted; trusted policy disables them"
        in _approval_errors(log)
    )

    # Every governed project must actually be judged, or its project
    # constitution got no live exercise at all.
    app_only = [
        event for event in _two_project_approval_events()
        if event.get("approvalId") != "report-tests"
    ]
    log.write_text("".join(json.dumps(event) + "\n" for event in app_only))
    assert "no approval from inventory-report was judged" in _approval_errors(log)

    denied_everything = _two_project_approval_events()
    for event in denied_everything:
        if event["type"] == "approval.resolved":
            event["verdict"] = "deny"
    log.write_text("".join(json.dumps(event) + "\n" for event in denied_everything))
    assert (
        "no approval request was approved; the children could not work"
        in _approval_errors(log)
    )


def test_approval_summary_reports_what_the_children_asked_for(tmp_path: Path):
    log = tmp_path / "service.jsonl"
    events = _two_project_approval_events()
    log.write_text("".join(json.dumps(event) + "\n" for event in events))

    summary = _approval_summary(log)
    assert summary["total"] == 3
    assert summary["byProject"]["inventory-app"] == {"deny": 1, "approve_once": 1}
    assert summary["byProject"]["inventory-report"] == {"approve_once": 1}
    assert [entry["command"] for entry in summary["denied"]] == ["pip install rich"]
    assert summary["denied"][0]["project"] == "inventory-app"
    assert summary["policyAllowed"] == {}

    # Commands decided by rule are reported separately: they are not judged
    # approvals and must not inflate or deflate the approval counts.
    allowed = {
        "type": "approval.allowed_by_policy",
        "project": "/work/inventory-report",
        "request": {"command": "/bin/zsh -lc 'sed -n 1,5p README.md'"},
        "execPolicy": {"justifications": ["range reads"]},
    }
    log.write_text("".join(json.dumps(event) + "\n" for event in [allowed, *events, allowed]))
    summary = _approval_summary(log)
    assert summary["total"] == 3
    assert summary["policyAllowed"] == {"inventory-report": 2}
    assert _approval_errors(log) == []


def test_human_output_shows_rule_allowed_commands(capsys):
    renderer = OutputRenderer()
    renderer.service({
        "type": "approval.allowed_by_policy",
        "project": "/tmp/inventory-app",
        "request": {"command": "/bin/zsh -lc 'sed -n 1,5p README.md'"},
        "execPolicy": {"justifications": ["range reads of the project's own files"]},
    })
    renderer.harness(
        "live_e2e.completed", returnCode=0, result={},
        approvals={"total": 8, "byProject": {}, "denied": [], "policyAllowed": {"inventory-app": 5}},
        validationErrors=[],
    )
    output = capsys.readouterr().out
    # The layer that decided it leads the line: an operator rule, not a judge.
    assert "rule/allow" in output
    assert "/bin/zsh -lc 'sed -n 1,5p README.md'" in output
    assert "range reads of the project's own files" in output
    assert "Approvals judged: 8" in output
    assert "Commands allowed by exec policy without a judge: 5" in output


def test_human_output_distinguishes_code_accepted_file_changes(capsys):
    """Judged approvals, rule-allowed commands, and code-decided file changes."""
    renderer = OutputRenderer()
    renderer.service({
        "type": "approval.allowed_by_policy",
        "method": "item/fileChange/requestApproval",
        "project": "/tmp/inventory-app",
        "request": {"changes": [{"path": "/tmp/inventory-app/inventory_app/domain.py"}]},
        "containment": {
            "rule": "accepted by containment: every change path normalizes inside it",
            "paths": ["/tmp/inventory-app/inventory_app/domain.py"],
        },
    })
    renderer.service({
        "type": "approval.declined_by_policy",
        "method": "item/fileChange/requestApproval",
        "project": "/tmp/inventory-report",
        "request": {"changes": [{"path": "/tmp/inventory-report/report.py"}]},
        "containment": {
            "rule": "declined by containment: sandbox mode is read-only",
            "paths": ["/tmp/inventory-report/report.py"],
        },
    })
    renderer.harness(
        "live_e2e.completed", returnCode=0, result={},
        approvals={
            "total": 8, "byProject": {}, "denied": [],
            "policyAllowed": {"inventory-app": 5},
            "fileChangesAccepted": {"inventory-app": 11, "inventory-report": 6},
            "fileChangesDeclined": {"inventory-report": 1},
        },
        validationErrors=[],
    )
    output = capsys.readouterr().out
    # Containment is code, not a rule and not a judge.
    assert "code/allow" in output and "1 file: domain.py" in output
    assert "code/deny" in output and "1 file: report.py" in output
    assert "declined by containment: sandbox mode is read-only" in output
    assert "In-project file changes accepted by code without a judge: 17" in output
    assert "File changes declined by code without a judge: 1" in output


def test_a_judged_in_project_file_change_is_a_validation_error(tmp_path: Path):
    """Zero judged in-project file changes, unless an operator rule asked for one."""
    log = tmp_path / "service.jsonl"
    events = _two_project_approval_events()
    judged_change = _approval(events, "install", "requested")
    judged_change["method"] = "item/fileChange/requestApproval"
    log.write_text("".join(json.dumps(event) + "\n" for event in events))
    assert "an in-project file change from inventory-app reached a judge; " in (
        _approval_errors(log)[0]
    )

    judged_change["containment"] = {
        "rule": "escalated by operator rule: supplied tests",
        "escalatedBy": [{
            "path": "/work/inventory-app/test_inventory_app.py",
            "declared": "test_inventory_app.py",
            "justification": "supplied tests",
        }],
    }
    log.write_text("".join(json.dumps(event) + "\n" for event in events))
    assert _approval_errors(log) == []


def test_approval_validation_requires_per_project_constitution_provenance(tmp_path: Path):
    log = tmp_path / "service.jsonl"

    missing = _two_project_approval_events()
    del _approval(missing, "install", "requested")["policy"]
    _approval(missing, "install", "resolved")["policy"] = {
        "overall": {"source": "x", "digest": "overall00"}, "project": None,
    }
    log.write_text("".join(json.dumps(event) + "\n" for event in missing))
    assert _approval_errors(log) == [
        "approval.requested install records no policy provenance",
        "approval.resolved install names no project constitution for /work/inventory-app",
    ]

    # One project judged against the other's constitution is the failure the
    # two-tier design exists to prevent, so the harness must catch it.
    leaked = _two_project_approval_events()
    for kind in ("requested", "resolved"):
        _approval(leaked, "report-tests", kind)["policy"] = _policy("app00000", "inventory-app")
    log.write_text("".join(json.dumps(event) + "\n" for event in leaked))
    assert _approval_errors(log) == [
        "two projects were judged against the same project constitution"
    ]

    # A judge given a different overall document per request means the ceiling
    # is not shared, which the harness must also catch.
    split = _two_project_approval_events()
    resolved = _approval(split, "tests", "resolved")
    resolved["policy"] = dict(
        resolved["policy"], overall={"source": "/operator/other.md", "digest": "other000"}
    )
    log.write_text("".join(json.dumps(event) + "\n" for event in split))
    assert _approval_errors(log) == ["approvals cite more than one overall constitution"]


def test_approval_validation_requires_the_task_each_request_was_judged_against(tmp_path: Path):
    """A necessity verdict is only auditable if the task is on the record (#0002)."""
    log = tmp_path / "service.jsonl"

    missing = _two_project_approval_events()
    del _approval(missing, "install", "requested")["assignment"]
    log.write_text("".join(json.dumps(event) + "\n" for event in missing))
    assert _approval_errors(log) == [
        "approval.requested install records no task assignment; the judge was "
        "asked whether an action was necessary for an unstated task"
    ]

    # A digest no session prompt produced means the descriptor did not come
    # from the coordination path this run actually drove.
    forged = _two_project_approval_events()
    _approval(forged, "tests", "requested")["assignment"] = _assignment("forged00", turn=2)
    log.write_text("".join(json.dumps(event) + "\n" for event in forged))
    assert _approval_errors(log) == [
        "approval.requested tests cites an assignment this run never sent to "
        "session session-app"
    ]

    # A follow-up turn is legitimate as long as its prompt is on the record.
    follow_up = _two_project_approval_events()
    _approval(follow_up, "tests", "requested")["assignment"] = _assignment("secondtn", turn=2)
    _approval(follow_up, "tests", "resolved")["assignment"] = _assignment("secondtn", turn=2)
    follow_up.append({
        "type": "session.turn_started",
        "session": {"id": "session-app", "project": "/work/inventory-app"},
        "prompt": "The suite still fails on rounding; fix only that.",
        "assignment": _assignment("secondtn", turn=2),
    })
    log.write_text("".join(json.dumps(event) + "\n" for event in follow_up))
    assert _approval_errors(log) == []

    # The approval record cites the prompt; it must not restate it.
    repeated = _two_project_approval_events()
    _approval(repeated, "install", "resolved")["assignment"] = {
        **_assignment("apptask0"), "text": "Implement the three functions marked TODO.",
    }
    log.write_text("".join(json.dumps(event) + "\n" for event in repeated))
    assert _approval_errors(log) == [
        "approval.resolved install repeats the assignment text"
    ]


def test_coordinator_project_carries_no_codex_configuration():
    """The one root the coordinator can write declares none of its boundary."""
    repo = Path(__file__).resolve().parents[1]
    assert not (repo / "examples/coordinator/.codex").exists()


def test_live_e2e_allows_its_generated_non_git_workspace():
    source = inspect.getsource(run)
    assert '"--skip-git-repo-check"' in source


def test_the_rendered_codex_home_holds_every_profile_a_run_selects(tmp_path: Path):
    """The boundary of a live run is readable in one operator-owned file.

    Before #0021 the coordinating session's profile was assembled from six
    `--config` flags, because a profile left in the working directory is
    silently ignored. A home removes the need for them, and puts the worker
    profiles beside the coordinating one.
    """
    repo = Path(__file__).resolve().parents[1]
    auth = tmp_path / "auth.json"
    auth.write_text("{}")
    home = _render_codex_home(tmp_path, repo / "examples", auth=auth)

    rendered = tomllib.loads((home / "config.toml").read_text())
    assert rendered["default_permissions"] == ":read-only"
    # Every domain and unix-socket grant below is inert without this.
    assert rendered["features"]["network_proxy"] is True
    profiles = rendered["permissions"]
    assert COORDINATOR_PROFILE in profiles
    coordinator_network = profiles[COORDINATOR_PROFILE]["network"]
    assert coordinator_network["allow_local_binding"] is True
    # The service owns the app-server connection and is not sandboxed; a
    # coordinating session that could reach the control socket could drive the
    # runtime directly and bypass judging entirely.
    assert "unix_sockets" not in coordinator_network
    assert "domains" not in coordinator_network
    # The trusted session keeps its temporary roots: a live run showed that
    # demoting them makes tempfile.TemporaryDirectory() fail outright.
    assert "filesystem" not in profiles[COORDINATOR_PROFILE]
    for worker in ("worker_workspace", "worker_pypi"):
        profile = profiles[worker]
        assert profile["extends"] == ":workspace"
        # :workspace alone leaves both temporary roots writable; the boundary
        # this project enforced with a sandbox literal did not.
        assert profile["filesystem"] == {":tmpdir": "read", ":slash_tmp": "read"}
        assert "unix_sockets" not in profile.get("network", {})
    assert profiles["worker_workspace"]["network"]["enabled"] is False
    network = profiles["worker_pypi"]["network"]
    assert network["mode"] == "limited"
    # Neither loopback path into this system is open to a worker.
    assert network["allow_local_binding"] is False
    # Both PyPI hosts, and nothing else: the index and the wheel store. Allowing
    # only the first gets an index hit followed by a refused download.
    assert sorted(network["domains"]) == ["files.pythonhosted.org", "pypi.org"]
    assert not any("danger-full-access" in str(profile) for profile in profiles.values())
    # Auth lives in the home: a fresh one reports "Not logged in".
    assert (home / "auth.json").is_symlink()
    assert home.stat().st_mode & 0o077 == 0


def test_the_rendered_home_is_a_copy_so_a_run_cannot_rewrite_the_checked_in_source(tmp_path: Path):
    """The runtime writes trust records and sqlite state into CODEX_HOME."""
    repo = Path(__file__).resolve().parents[1]
    source = repo / "examples/operator/codex-home/config.toml"
    before = source.read_bytes()

    auth = tmp_path / "auth.json"
    auth.write_text("{}")
    home = _render_codex_home(tmp_path, repo / "examples", auth=auth)

    assert (home / "config.toml").resolve() != source.resolve()
    assert not (home / "config.toml").is_symlink()
    assert source.read_bytes() == before
    # The home names no runtime path at all, so there is nothing to render in.
    assert "{{" not in before.decode()


def test_started_processes_do_not_inherit_a_stdin_that_may_never_close():
    """`codex exec` waits for EOF on a pipe, and the coordinator is untimed."""
    source = inspect.getsource(run)
    assert source.count("stdin=asyncio.subprocess.DEVNULL") == 2


def test_coordinator_command_carries_its_permission_profile():
    """A profile inside the coordinator's project would be silently ignored."""
    source = inspect.getsource(run)
    assert '\'default_permissions="{COORDINATOR_PROFILE}"\'' in source
    # No reviewer is watching the coordinating session to answer a request.
    assert '\'approval_policy="never"\'' in source
    # Never the six-flag form this replaced: the profile body lives in the home.
    assert "permissions.coordinator" not in source
    assert '.codex/config.toml' not in source


def test_every_child_of_a_live_run_reads_the_rendered_home():
    """A run that reads ~/.codex is evidence about one machine, not a boundary."""
    source = inspect.getsource(run)
    assert source.count("env=run_environment") == 2
    assert '"CODEX_HOME": str(codex_home)' in source


def test_idle_service_log_is_reported_as_an_unreachable_control_plane(tmp_path: Path):
    log = tmp_path / "service.jsonl"
    log.write_text(json.dumps({"sequence": 1, "type": "service.started"}) + "\n")

    running = _control_plane_error(log, 8765, True)
    stopped = _control_plane_error(log, 8765, False)

    assert running is not None and "still running and idle" in running
    assert "8765" in running
    assert stopped is not None and "no longer running" in stopped


def test_a_used_control_plane_reports_no_reachability_error(tmp_path: Path):
    log = tmp_path / "service.jsonl"
    log.write_text(
        json.dumps({"sequence": 1, "type": "service.started"}) + "\n"
        + json.dumps({"sequence": 2, "type": "session.created"}) + "\n"
    )

    assert _control_plane_error(log, 8765, True) is None


def test_the_live_harness_names_only_things_that_exist():
    """Nothing offline executes `run`, so a stale name costs a whole live run.

    This is not hypothetical: moving the harness onto a private listener left
    `run` calling a helper it no longer imported, and every test still passed
    because the failure lived in a function only a live run reaches.
    """
    import builtins
    import symtable

    import codex_coordinator.live_e2e as module

    source = Path(module.__file__).read_text()
    table = symtable.symtable(source, module.__file__, "exec")

    def unresolved(scope) -> list[str]:
        missing = [
            symbol.get_name() for symbol in scope.get_symbols()
            if symbol.is_global() and not symbol.is_assigned()
            and not hasattr(module, symbol.get_name())
            and not hasattr(builtins, symbol.get_name())
        ]
        for child in scope.get_children():
            missing.extend(unresolved(child))
        return missing

    assert sorted(set(unresolved(table))) == []


def test_the_network_gate_runs_before_the_listener_is_torn_down():
    """A refusal has to be the sandbox's, not a missing file's.

    A live run caught this: with the probe after teardown, the connect to the
    control socket failed with ENOENT before the sandbox's socket policy was
    consulted at all, which would read as a passing refusal while proving
    nothing about the boundary.
    """
    source = inspect.getsource(run)
    probe = source.index("_network_boundary_report(")
    teardown = source.index("os.killpg(listener.pid")
    assert probe < teardown


def test_a_passing_boundary_check_is_reported_rather_than_silent(capsys):
    """A run that checked and a run that did not must not look the same.

    The network and isolation assertions contribute only to `validationErrors`,
    so on success they used to print nothing at all — and a reader of the
    default output had no way to tell an enforced ceiling from one that was
    never exercised. That is the same silently-inert failure this project
    refuses in a permission profile, so it is refused in the harness too.
    """
    renderer = OutputRenderer()
    renderer.harness(
        "live_e2e.completed", returnCode=0, workspace="/tmp/w", result={"ok": True},
        approvals={}, validationErrors=[],
        networkBoundary={
            "allowed_restore": "ok:restored",
            "refused_restore": "RuntimeError:refused by the proxy: 403 Forbidden",
            "control_socket": "PermissionError:[Errno 1] Operation not permitted",
            "service_port": "PermissionError:[Errno 1] Operation not permitted",
            "offline_allowed_restore": "RuntimeError:no network at all: the host did not resolve",
            "offline_control_socket": "PermissionError:[Errno 1] Operation not permitted",
            "offline_service_port": "PermissionError:[Errno 1] Operation not permitted",
        },
        isolation={
            "home": "/tmp/w/codex-home", "trustRecord": True,
            "sqlite": ["state_5.sqlite"], "developerHomeUnchanged": True,
        },
    )

    printed = capsys.readouterr().out
    assert "Worker network boundaries" in printed
    for expected in (
        "worker_pypi: declared restore from PyPI: restored — ok:restored",
        "worker_pypi: a requirement from any other host: refused at the proxy",
        "worker_workspace: any install at all: no network to reach",
        "app-server control socket: refused by the sandbox — PermissionError",
        "coordination service port: refused by the sandbox — PermissionError",
    ):
        assert expected in printed, printed
    assert "Codex home isolation: /tmp/w/codex-home" in printed
    assert "trust record True, 1 sqlite files" in printed
    assert "NOT CHECKED" not in printed


def test_a_boundary_check_that_did_not_run_says_so(capsys):
    """Absence of a result is reported as absence, never as a pass."""
    renderer = OutputRenderer()
    renderer.harness(
        "live_e2e.completed", returnCode=0, workspace="/tmp/w", result={"ok": True},
        approvals={}, validationErrors=[],
    )

    printed = capsys.readouterr().out
    assert "Worker network boundary: NOT CHECKED" in printed
    assert "Codex home isolation: NOT CHECKED" in printed


def test_a_worker_that_could_not_restore_fails_the_run_before_it_starts(tmp_path: Path):
    """A broken environment must not read as a refused restore.

    The projects restore into a `.venv` of their own, so what a worker needs is
    an interpreter whose bundled ensurepip can seed one — not an ambient pip.
    Without this check the run would report a failed restore, and nothing would
    distinguish "this interpreter cannot build a venv" from "the network ceiling
    refused the host".
    """
    import codex_coordinator.live_e2e as module

    assert module._require_worker_venv(dict(os.environ)).endswith("python3")

    # An interpreter that exists but cannot create an environment.
    broken = tmp_path / "bin"
    broken.mkdir()
    (broken / "python3").symlink_to(shutil.which("false") or "/usr/bin/false")
    with pytest.raises(RuntimeError, match="cannot create a virtual environment"):
        module._require_worker_venv({**os.environ, "PATH": str(broken)})

    # No interpreter at all is reported as such, not as a broken environment.
    bare = tmp_path / "bare"
    bare.mkdir()
    with pytest.raises(RuntimeError, match="no python3 on the PATH"):
        module._require_worker_venv({**os.environ, "PATH": str(bare)})


def _proxy_log(home: Path, rows: list[tuple[int, str, str]]) -> None:
    """A stand-in for the runtime's own log database, in its own shape."""
    import sqlite3

    connection = sqlite3.connect(home / "logs_2.sqlite")
    connection.execute("create table logs (id integer primary key, target text, feedback_log_body text)")
    connection.executemany("insert into logs values (?, ?, ?)", rows)
    connection.commit()
    connection.close()


def _decision(thread: str, host: str, decision: str, reason: str) -> str:
    return (
        'event.name="codex.network_proxy.policy_decision" '
        f'conversation.id="{thread}" '
        'network.policy.scope="domain" '
        f'network.policy.decision="{decision}" '
        f'network.policy.reason="{reason}" '
        'network.transport.protocol="https_connect" '
        f'server.address="{host}" server.port=443'
    )


def test_proxy_decisions_are_read_from_the_runtime_log(tmp_path: Path):
    """The sandbox's own verdicts, in the shape the runtime records them."""
    from codex_coordinator.live_e2e import PROXY_LOG_TARGET, _proxy_decisions

    home = tmp_path / "codex-home"
    home.mkdir()
    assert _proxy_decisions(home, 0) == []  # No database yet is not an error.
    _proxy_log(home, [
        (1, "some.other.target", "unrelated"),
        (2, PROXY_LOG_TARGET, _decision("thread-a", "pypi.org", "allow", "allow")),
        (3, PROXY_LOG_TARGET, _decision("thread-b", "github.com", "deny", "not_allowed")),
    ])

    decisions = _proxy_decisions(home, 0)

    assert [(d["thread"], d["host"], d["decision"], d["reason"]) for d in decisions] == [
        ("thread-a", "pypi.org", "allow", "allow"),
        ("thread-b", "github.com", "deny", "not_allowed"),
    ]
    # Only what is new, so a relay does not repeat itself every poll.
    assert _proxy_decisions(home, 2) == [decisions[1]]


def test_a_proxy_decision_names_the_worker_it_was_made_for(capsys):
    """A network refusal has to land beside that worker's judged approvals.

    The proxy records against the thread and the service against the session,
    so without this the two enforcement layers could not be read as one
    timeline — which is the whole reason for interleaving them.
    """
    renderer = OutputRenderer()
    renderer.service({
        "type": "session.started",
        "session": {"id": "s1", "threadId": "thread-a", "project": "/tmp/inventory-app"},
        "prompt": "Build it.",
    })

    renderer.proxy({
        "thread": "thread-a", "host": "pypi.org", "port": "443",
        "decision": "allow", "reason": "allow",
    })
    renderer.proxy({
        "thread": "thread-a", "host": "github.com", "port": "443",
        "decision": "deny", "reason": "not_allowed",
    })

    printed = capsys.readouterr().out
    assert "proxy/allow" in printed and "pypi.org:443" in printed
    assert "proxy/deny" in printed and "github.com:443" in printed
    # Attributed, so it reads beside that worker's judged lines.
    assert printed.count("inventory-app") >= 3
    # A refusal says why; an allow needs no excuse.
    assert "reason: not_allowed" in printed


def test_every_timeline_tag_fits_its_column(capsys):
    """A tag that overflows pushes every later field out of line.

    The tag is the column a reader scans, so the timeline is only readable if
    they all align — `session/completed` did not, which is why a finished turn
    renders as `session/done`.
    """
    renderer = OutputRenderer()
    renderer.service({
        "type": "session.started",
        "session": {"id": "s1", "threadId": "t1", "project": "/tmp/inventory-report"},
        "prompt": "go",
    })
    renderer.service({
        "type": "app_server.notification", "method": "turn/completed", "sessionId": "s1",
        "message": {"params": {"turn": {"status": "completed", "items": []}}},
    })
    renderer.service({"type": "approval.requested", "project": "/tmp/inventory-report",
                      "request": {"command": "x"}})
    renderer.service({"type": "approval.resolved", "sessionId": "s1",
                      "verdict": "approve_once", "reason": "fine"})
    renderer.proxy({"thread": "t1", "host": "pypi.org", "port": "443",
                    "decision": "allow", "reason": "allow"})

    for line in capsys.readouterr().out.splitlines():
        body = line.split("] ", 1)[-1]
        if not body or body.startswith(" "):
            continue
        tag = body.split(" ", 1)[0]
        assert len(tag) <= OutputRenderer.TAG_WIDTH, tag
