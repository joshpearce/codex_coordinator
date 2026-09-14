from pathlib import Path
import subprocess

import pytest

from codex_coordinator.config import OperatorConfig
from codex_coordinator.preflight import check


def _project(root: Path, name: str) -> Path:
    """A worker project directory; its boundary is declared by the operator."""
    project = root / name
    project.mkdir(parents=True)
    return project


def test_preflight_checks_operator_project_and_codex(monkeypatch, tmp_path: Path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    worker = _project(allowed, "worker")
    outside = _project(tmp_path, "outside")
    config = OperatorConfig.load(
        environ={}, overrides={"allowed_roots": [str(allowed)], "socket_path": str(tmp_path / "missing.sock")},
    )
    monkeypatch.setattr("codex_coordinator.preflight.check_codex_compatibility", lambda _command: "0.154.0")
    monkeypatch.setattr("codex_coordinator.preflight.shutil.which", lambda _command: "/bin/codex")
    monkeypatch.setattr("codex_coordinator.preflight.default_daemon_socket", lambda: config.socket_path)

    class Result:
        returncode = 0

    monkeypatch.setattr("codex_coordinator.preflight.subprocess.run", lambda *_args, **_kwargs: Result())
    report = check(config, [worker])
    assert report["ok"] and report["codexVersion"] == "0.154.0"
    # With nothing declared for this project, the operator-wide default applies
    # and preflight names it, so the boundary is visible before any session runs.
    assert report["projects"] == [{
        "project": str(worker),
        "sandboxMode": "workspace-write",
        "approvalPolicy": "on-request",
        "permissionsSource": "operator-wide default",
        "permissionsDigest": config.default_worker_permissions.digest,
        "execPolicy": None,
    }]
    assert report["socketReady"] is False
    with pytest.raises(ValueError, match="outside configured"):
        check(config, [outside])
    with pytest.raises(ValueError, match="socket is unavailable"):
        check(config, [worker], require_socket=True)
    monkeypatch.setattr("codex_coordinator.preflight.default_daemon_socket", lambda: tmp_path / "default.sock")
    with pytest.raises(ValueError, match="custom Codex socket is unavailable"):
        check(config, [worker])


def test_preflight_rejects_existing_but_unreachable_socket(monkeypatch, tmp_path: Path):
    socket_path = tmp_path / "stale.sock"
    socket_path.write_text("stale")
    config = OperatorConfig.load(environ={}, overrides={
        "allowed_roots": [str(tmp_path)], "socket_path": str(socket_path),
    })
    monkeypatch.setattr("codex_coordinator.preflight.check_codex_compatibility", lambda _command: "0.154.0")
    monkeypatch.setattr("codex_coordinator.preflight.shutil.which", lambda _command: "/bin/codex")

    class Result:
        returncode = 0

    monkeypatch.setattr("codex_coordinator.preflight.subprocess.run", lambda *_args, **_kwargs: Result())

    def unreachable(_path):
        raise ConnectionError("stale app-server listener")

    monkeypatch.setattr("codex_coordinator.preflight.probe_local_socket", unreachable)
    with pytest.raises(ConnectionError, match="stale app-server listener"):
        check(config, [], require_socket=True)


def test_preflight_reports_executable_and_login_probe_failures(monkeypatch, tmp_path: Path):
    config = OperatorConfig.load(environ={}, overrides={"allowed_roots": [str(tmp_path)]})
    monkeypatch.setattr("codex_coordinator.preflight.check_codex_compatibility", lambda _command: "0.154.0")
    monkeypatch.setattr("codex_coordinator.preflight.shutil.which", lambda _command: None)
    with pytest.raises(ValueError, match="disappeared after compatibility check"):
        check(config, [])

    monkeypatch.setattr("codex_coordinator.preflight.shutil.which", lambda _command: "/bin/codex")

    def expired(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("codex login status", 10)

    monkeypatch.setattr("codex_coordinator.preflight.subprocess.run", expired)
    with pytest.raises(ValueError, match="cannot check Codex sign-in"):
        check(config, [])


def test_preflight_reports_two_tier_constitution_coverage(monkeypatch, tmp_path: Path):
    operator = tmp_path / "operator"
    coordinator = tmp_path / "coordinator"
    for directory in (operator, coordinator):
        directory.mkdir()
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    governed = _project(allowed, "governed")
    nested = _project(allowed, "nested")
    (operator / "constitution.md").write_text("No worker may use the network.\n")
    (operator / "allowed.md").write_text("These workers run their own tests.\n")
    config_path = operator / "operator.toml"
    config_path.write_text(
        'approval_mode = "service"\n'
        f'constitution_path = "{operator / "constitution.md"}"\n'
        f'coordinator_root = "{coordinator}"\n'
        f'allowed_roots = ["{allowed}"]\n'
        "[project_constitutions]\n"
        f'"{allowed}" = "{operator / "allowed.md"}"\n'
    )
    config = OperatorConfig.load(path=config_path, environ={})
    monkeypatch.setattr("codex_coordinator.preflight.check_codex_compatibility", lambda _command: "0.154.0")
    monkeypatch.setattr("codex_coordinator.preflight.shutil.which", lambda _command: "/bin/codex")
    monkeypatch.setattr("codex_coordinator.preflight.default_daemon_socket", lambda: config.socket_path)

    class Result:
        returncode = 0

    monkeypatch.setattr("codex_coordinator.preflight.subprocess.run", lambda *_args, **_kwargs: Result())
    report = check(config, [governed, nested])
    assert report["constitutionConfigured"] is True
    # An operator can see which policy file governs each registered project
    # before starting the service.
    assert report["projectConstitutions"] == [str(allowed)]
    assert config.constitution.for_project(governed).source == str(operator / "allowed.md")
    assert config.constitution.for_project(nested).source == str(operator / "allowed.md")


def test_preflight_reports_the_rules_decided_without_a_judge(monkeypatch, tmp_path: Path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    worker = _project(allowed, "worker")
    (tmp_path / "worker.rules").write_text(
        'prefix_rule(pattern=["sed", "-n"], decision="allow", justification="reads")\n'
    )
    (tmp_path / "worker.permissions.toml").write_text('exec_policy = "worker.rules"\n')
    config_path = tmp_path / "operator.toml"
    config_path.write_text(
        f'allowed_roots = ["{allowed}"]\n'
        f'socket_path = "{tmp_path / "missing.sock"}"\n'
        "[worker_permissions]\n"
        f'"{worker}" = "{tmp_path / "worker.permissions.toml"}"\n'
    )
    config = OperatorConfig.load(path=config_path, environ={})
    monkeypatch.setattr("codex_coordinator.preflight.check_codex_compatibility", lambda _command: "0.154.0")
    monkeypatch.setattr("codex_coordinator.preflight.shutil.which", lambda _command: "/bin/codex")
    monkeypatch.setattr("codex_coordinator.preflight.default_daemon_socket", lambda: config.socket_path)

    class Result:
        returncode = 0

    monkeypatch.setattr("codex_coordinator.preflight.subprocess.run", lambda *_args, **_kwargs: Result())
    report = check(config, [worker])
    exec_policy = config.permissions_for(worker).exec_policy
    assert exec_policy is not None
    assert report["projects"][0]["execPolicy"] == {
        "source": str(tmp_path / "worker.rules"), "digest": exec_policy.digest,
        "rules": 1, "escalations": 0,
    }
