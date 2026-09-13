import asyncio
import inspect
import json
import re
import shutil
import stat
import tomllib
from pathlib import Path

import pytest

from codex_coordinator.live_e2e import (
    OutputRenderer,
    _approval_errors,
    _relay_coordinator_events,
    _relay_service_events,
    _resolve_template,
    run,
)
from codex_coordinator.config import OperatorConfig


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
    assert re.findall(r"{{([A-Z_]+)}}", report_template) == [
        "INVENTORY_APP_PATH"
    ]

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
    assert str(inventory_app) in (
        coordinator / "goals/inventory-report.md"
    ).read_text()


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
    assert "Started inventory-app" in output
    assert "Build the inventory app." in output
    assert "Run its tests." in output
    assert "noisy token" not in output


def test_approval_validation_requires_verdicts_for_the_intended_requests(tmp_path: Path):
    log = tmp_path / "service.jsonl"
    events = [
        {
            "type": "approval.requested",
            "approvalId": "network",
            "request": {"command": "curl -I https://example.com"},
        },
        {
            "type": "approval.resolved",
            "approvalId": "network",
            "verdict": "deny",
        },
        {
            "type": "approval.requested",
            "approvalId": "tests",
            "request": {"command": "python -m unittest discover -v"},
        },
        {
            "type": "approval.resolved",
            "approvalId": "tests",
            "verdict": "approve_once",
        },
    ]
    log.write_text("".join(json.dumps(event) + "\n" for event in events))

    assert _approval_errors(log) == []

    events[0]["request"]["command"] = "python local_check.py"
    log.write_text("".join(json.dumps(event) + "\n" for event in events))
    assert _approval_errors(log) == [
        "no network approval request was explicitly denied"
    ]


def test_coordinator_template_has_safe_baseline():
    repo = Path(__file__).resolve().parents[1]
    config = (repo / "examples/coordinator/.codex/config.toml").read_text()
    assert 'default_permissions = "coordinator"' in config
    assert 'extends = ":workspace"' in config
    assert '"{{APP_SERVER_SOCKET}}" = "allow"' in config
    assert 'sandbox_mode = "danger-full-access"' not in config


def test_live_e2e_allows_its_generated_non_git_workspace():
    source = inspect.getsource(run)
    assert '"--skip-git-repo-check"' in source


def test_coordinator_config_resolves_only_the_socket_path(tmp_path: Path):
    repo = Path(__file__).resolve().parents[1]
    config = tmp_path / "config.toml"
    shutil.copy(repo / "examples/coordinator/.codex/config.toml", config)
    socket = tmp_path / "app-server-control.sock"

    rendered = _resolve_template(config, {"APP_SERVER_SOCKET": socket})

    assert "{{" not in rendered
    assert f'"{socket}" = "allow"' in rendered
    parsed = tomllib.loads(rendered)
    profile = parsed["permissions"]["coordinator"]
    assert profile["extends"] == ":workspace"
    assert profile["network"]["enabled"] is True
    assert profile["network"]["unix_sockets"][str(socket)] == "allow"
