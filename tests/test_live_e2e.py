import asyncio
import json
import re
import shutil
from pathlib import Path

import pytest

from codex_coordinator.live_e2e import _relay_service_events, _resolve_template


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

    await _relay_service_events(log, stop)

    record = json.loads(capsys.readouterr().out)
    assert record == {
        "type": "live_e2e.service_event",
        "event": {"type": "service.started", "port": 1234},
    }
