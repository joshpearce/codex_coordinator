import asyncio
import json
import re
import shutil
import tomllib
from pathlib import Path

import pytest

from codex_coordinator.live_e2e import (
    OutputRenderer,
    _approval_errors,
    _relay_service_events,
    _resolve_template,
)


def test_checked_in_goals_only_template_runtime_paths(tmp_path: Path):
    repo = Path(__file__).resolve().parents[1]
    source = repo / "examples/coordinator"
    coordinator = tmp_path / "coordinator"
    inventory_app = tmp_path / "inventory-app"
    inventory_report = tmp_path / "inventory-report"
    shutil.copytree(source, coordinator)

    goal_template = (source / "goal.md").read_text()
    report_template = (source / "goals/inventory-report.md").read_text()
    assert set(re.findall(r"{{([A-Z_]+)}}", goal_template)) == {
        "REPO_PATH",
        "COORDINATOR_PATH",
        "INVENTORY_APP_PATH",
        "INVENTORY_REPORT_PATH",
    }
    assert re.findall(r"{{([A-Z_]+)}}", report_template) == [
        "INVENTORY_APP_PATH"
    ]

    _resolve_template(
        coordinator / "goals/inventory-report.md",
        {"INVENTORY_APP_PATH": inventory_app},
    )
    rendered = _resolve_template(
        coordinator / "goal.md",
        {
            "REPO_PATH": repo,
            "COORDINATOR_PATH": coordinator,
            "INVENTORY_APP_PATH": inventory_app,
            "INVENTORY_REPORT_PATH": inventory_report,
        },
    )

    assert "{{" not in rendered
    assert str(inventory_app) in rendered
    assert str(inventory_report) in rendered
    assert str(inventory_app) in (
        coordinator / "goals/inventory-report.md"
    ).read_text()


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
