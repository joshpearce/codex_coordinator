from pathlib import Path

import pytest

from codex_coordinator.config import OperatorConfig
from codex_coordinator import preflight


def config(tmp_path: Path) -> OperatorConfig:
    project = tmp_path / "project"
    project.mkdir()
    return OperatorConfig.load(environ={}, overrides={
        "projects": {"alpha": str(project)},
        "codex_command": "codex",
        "socket_path": str(tmp_path / "app-server.sock"),
    })


@pytest.fixture(autouse=True)
def successful_codex_checks(monkeypatch):
    monkeypatch.setattr(preflight, "check_codex_compatibility", lambda _command: "test")
    monkeypatch.setattr(preflight.shutil, "which", lambda _command: "/bin/codex")
    monkeypatch.setattr(
        preflight.subprocess, "run",
        lambda *_args, **_kwargs: type("Result", (), {"returncode": 0})(),
    )


def test_metadata_access_denial_names_socket_and_permission(tmp_path, monkeypatch):
    selected = config(tmp_path)
    denied = PermissionError(1, "Operation not permitted", selected.socket_path)
    monkeypatch.setattr(Path, "lstat", lambda self: (_ for _ in ()).throw(denied))

    with pytest.raises(ValueError, match=r"cannot access host Codex app-server socket.*Operation not permitted"):
        preflight.check(selected, [], require_socket=True)


def test_connection_access_denial_does_not_recommend_another_daemon(tmp_path, monkeypatch):
    selected = config(tmp_path)
    monkeypatch.setattr(Path, "lstat", lambda self: object())
    denied = PermissionError(1, "Operation not permitted", selected.socket_path)
    monkeypatch.setattr(preflight, "probe_local_socket", lambda _path: (_ for _ in ()).throw(denied))

    with pytest.raises(ValueError) as failure:
        preflight.check(selected, [], require_socket=True)
    message = str(failure.value)
    assert str(selected.socket_path) in message
    assert "Operation not permitted" in message
    assert "start" not in message.lower()


def test_missing_invalid_and_healthy_socket(tmp_path, monkeypatch):
    selected = config(tmp_path)
    with pytest.raises(ValueError, match="start the app-server"):
        preflight.check(selected, [], require_socket=True)

    selected.socket_path.touch()
    monkeypatch.setattr(preflight, "probe_local_socket", lambda _path: (_ for _ in ()).throw(ValueError("not a Unix socket")))
    with pytest.raises(ValueError, match="not a Unix socket"):
        preflight.check(selected, [], require_socket=True)

    monkeypatch.setattr(preflight, "probe_local_socket", lambda _path: None)
    result = preflight.check(selected, [], require_socket=True)
    assert result["socketReady"] is True


def test_cli_expected_failure_is_concise_without_traceback(tmp_path, monkeypatch, capsys):
    selected = config(tmp_path)
    monkeypatch.setattr(preflight.OperatorConfig, "load", lambda **_kwargs: selected)
    monkeypatch.setattr(
        preflight, "check",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("socket denied")),
    )
    monkeypatch.setattr("sys.argv", ["codex-coordinator-preflight", "--require-socket"])

    with pytest.raises(SystemExit) as stopped:
        preflight.main()
    assert stopped.value.code == 2
    stderr = capsys.readouterr().err
    assert "preflight failed: socket denied" in stderr
    assert "Traceback" not in stderr
