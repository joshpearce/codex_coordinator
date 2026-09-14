from pathlib import Path
from types import SimpleNamespace

import pytest

from codex_coordinator.cli import run
from codex_coordinator.config import OperatorConfig


def _args(project: Path) -> SimpleNamespace:
    return SimpleNamespace(
        project=project, prompt="do the work", judge_policy=None,
        config=None, allowed_root=None, socket=None, codex_command=None,
        worker_model=None, worker_reasoning_effort=None,
        judge_timeout_seconds=None, timeout=1,
    )


@pytest.mark.asyncio
async def test_one_shot_cli_requires_trusted_root_before_project_config(monkeypatch, tmp_path: Path):
    config = OperatorConfig.load(environ={})
    monkeypatch.setattr("codex_coordinator.cli.OperatorConfig.load", lambda **_kwargs: config)

    def unexpected(_project):
        raise AssertionError("worker project config was read")

    monkeypatch.setattr("codex_coordinator.cli.WorkerPermissions.from_project", unexpected)
    with pytest.raises(ValueError, match="at least one allowed root"):
        await run(_args(tmp_path))


@pytest.mark.asyncio
async def test_one_shot_cli_rejects_symlink_escape_before_project_config(monkeypatch, tmp_path: Path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    escaped = allowed / "escaped"
    escaped.symlink_to(outside, target_is_directory=True)
    config = OperatorConfig.load(environ={}, overrides={"allowed_roots": [str(allowed)]})
    monkeypatch.setattr("codex_coordinator.cli.OperatorConfig.load", lambda **_kwargs: config)

    def unexpected(_project):
        raise AssertionError("worker project config was read")

    monkeypatch.setattr("codex_coordinator.cli.WorkerPermissions.from_project", unexpected)
    with pytest.raises(ValueError, match="outside the configured allowed roots"):
        await run(_args(escaped))


@pytest.mark.asyncio
async def test_one_shot_cli_reports_unreachable_socket(monkeypatch, tmp_path: Path):
    project = tmp_path / "worker"
    (project / ".codex").mkdir(parents=True)
    (project / ".codex/config.toml").write_text(
        'approval_policy = "on-request"\n'
        'approvals_reviewer = "user"\n'
        'sandbox_mode = "read-only"\n'
    )
    config = OperatorConfig.load(environ={}, overrides={"allowed_roots": [str(tmp_path)]})
    monkeypatch.setattr("codex_coordinator.cli.OperatorConfig.load", lambda **_kwargs: config)
    monkeypatch.setattr("codex_coordinator.cli.check_codex_compatibility", lambda _command: "0.154.0")

    async def noop(**_kwargs):
        return None

    async def unavailable(*_args, **_kwargs):
        raise ConnectionRefusedError("stale socket")

    monkeypatch.setattr("codex_coordinator.cli.ensure_daemon", noop)
    monkeypatch.setattr("codex_coordinator.cli.websockets.unix_connect", unavailable)
    with pytest.raises(ConnectionError, match="codex-coordinator-preflight --require-socket"):
        await run(_args(project))


@pytest.mark.asyncio
async def test_one_shot_cli_uses_operator_judge_settings(monkeypatch, tmp_path: Path):
    project = tmp_path / "worker"
    (project / ".codex").mkdir(parents=True)
    (project / ".codex/config.toml").write_text(
        'approval_policy = "on-request"\n'
        'approvals_reviewer = "user"\n'
        'sandbox_mode = "read-only"\n'
    )
    config = OperatorConfig.load(environ={}, overrides={
        "allowed_roots": [str(tmp_path)],
        "judge_policy": "Only approve reviewed commands.",
        "judge_timeout_seconds": 42,
    })
    monkeypatch.setattr("codex_coordinator.cli.OperatorConfig.load", lambda **_kwargs: config)
    monkeypatch.setattr("codex_coordinator.cli.check_codex_compatibility", lambda _command: "0.154.0")
    captured = {}

    class CapturingJudge:
        def __init__(self, runner, *, constitution):
            captured["runner"] = runner
            captured["constitution"] = constitution

    async def noop(**_kwargs):
        return None

    async def unavailable(*_args, **_kwargs):
        raise ConnectionRefusedError("stale socket")

    monkeypatch.setattr("codex_coordinator.cli.OneShotCodexJudge", CapturingJudge)
    monkeypatch.setattr("codex_coordinator.cli.ensure_daemon", noop)
    monkeypatch.setattr("codex_coordinator.cli.websockets.unix_connect", unavailable)
    with pytest.raises(ConnectionError, match="codex-coordinator-preflight"):
        await run(_args(project))
    constitution = captured["constitution"]
    assert constitution.overall.text == "Only approve reviewed commands."
    assert constitution.overall.source == "judge_policy"
    assert constitution.projects == {}
    assert not constitution.require_project_policy
    assert captured["runner"].keywords == {
        "codex_command": config.codex_command,
        "timeout_seconds": 42,
    }
