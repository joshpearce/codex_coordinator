import json
from pathlib import Path

import pytest

from codex_coordinator.config import OperatorConfig
from codex_coordinator.coordinator import ApprovalPolicy


def test_operator_config_precedence_and_sealed_permission_ceiling(tmp_path: Path):
    file_root = tmp_path / "file"
    env_root = tmp_path / "env"
    cli_root = tmp_path / "cli"
    for root in (file_root, env_root, cli_root):
        root.mkdir()
    allowed_path = cli_root / "allowed"
    config_path = tmp_path / "operator.toml"
    config_path.write_text(
        f'allowed_roots = ["{file_root}"]\n'
        'codex_command = "from-file"\n'
        'worker_reasoning_effort = "medium"\n'
        'approval_timeout_seconds = 10\n'
    )
    config = OperatorConfig.load(
        path=config_path,
        environ={
            "CODEX_COORDINATOR_ALLOWED_ROOTS": json.dumps([str(env_root)]),
            "CODEX_COORDINATOR_CODEX_COMMAND": "from-env",
            "CODEX_COORDINATOR_APPROVAL_TIMEOUT_SECONDS": "20",
        },
        overrides={
            "allowed_roots": [cli_root],
            "event_capacity": 32,
            "item_capacity": 8,
            "permission_ceilings": {
                str(cli_root): {"fileSystem": {"read": [str(allowed_path)]}},
            },
            "codex_command": "from-cli",
        },
    )

    assert config.allowed_roots == (cli_root.resolve(),)
    assert config.codex_command == "from-cli"
    assert config.worker_reasoning_effort == "medium"
    assert config.approval_timeout_seconds == 20
    assert config.event_capacity == 32
    assert config.item_capacity == 8
    with pytest.raises(TypeError):
        config.permission_ceilings[cli_root]["fileSystem"]["read"] += ("extra",)
    policy = ApprovalPolicy(cli_root, allowed_permissions=config.permission_ceilings[cli_root])
    assert policy.allowed_permissions["fileSystem"]["read"] == (str(allowed_path),)


def test_operator_config_reads_project_ceiling_from_toml(tmp_path: Path):
    project = tmp_path / "worker"
    project.mkdir()
    config_path = tmp_path / "operator.toml"
    config_path.write_text(
        f'allowed_roots = ["{project}"]\n'
        f'[permission_ceilings."{project}"]\n'
        f'fileSystem = {{ read = ["{project / "a"}"] }}\n'
    )
    config = OperatorConfig.load(path=config_path, environ={})
    assert config.permission_ceilings[project]["fileSystem"]["read"] == (
        str(project / "a"),
    )


@pytest.mark.parametrize(
    "content, error",
    [
        ("unknown = true\n", "unsupported operator config fields"),
        ('allowed_roots = ["relative/path"]\n', "must be an absolute path"),
        ("approval_timeout_seconds = 0\n", "must be a positive number"),
        ("item_capacity = 0\n", "integer of at least 1"),
        ("event_max_bytes = 100\n", "integer of at least 256"),
    ],
)
def test_operator_config_rejects_invalid_values(tmp_path: Path, content: str, error: str):
    config_path = tmp_path / "operator.toml"
    config_path.write_text(content)
    with pytest.raises(ValueError, match=error):
        OperatorConfig.load(path=config_path, environ={})


def test_operator_config_rejects_invalid_environment_json():
    with pytest.raises(ValueError, match="must contain JSON"):
        OperatorConfig.load(
            environ={"CODEX_COORDINATOR_ALLOWED_ROOTS": "not json"},
        )


def test_operator_config_rejects_worker_writable_or_symlinked_file(tmp_path: Path):
    root = tmp_path / "workers"
    root.mkdir()
    inside = root / "operator.toml"
    inside.write_text(f'allowed_roots = ["{root}"]\n')
    with pytest.raises(ValueError, match="outside worker-writable"):
        OperatorConfig.load(path=inside, environ={})

    outside = tmp_path / "operator.toml"
    outside.write_text(f'allowed_roots = ["{root}"]\n')
    link = tmp_path / "link.toml"
    link.symlink_to(outside)
    with pytest.raises(ValueError, match="owner-controlled regular file"):
        OperatorConfig.load(path=link, environ={})

    outside.chmod(0o666)
    try:
        with pytest.raises(ValueError, match="owner-controlled regular file"):
            OperatorConfig.load(path=outside, environ={})
    finally:
        outside.chmod(0o600)


def test_service_constitution_is_trusted_and_snapshotted(tmp_path: Path):
    operator = tmp_path / "operator"
    coordinator = tmp_path / "coordinator"
    worker = tmp_path / "worker"
    for directory in (operator, coordinator, worker):
        directory.mkdir()
    constitution = operator / "constitution.md"
    constitution.write_text("Deny network requests.\n")
    config_path = operator / "operator.toml"
    config_path.write_text(
        f'approval_mode = "service"\n'
        f'constitution_path = "{constitution}"\n'
        f'coordinator_root = "{coordinator}"\n'
        f'allowed_roots = ["{worker}"]\n'
    )
    config = OperatorConfig.load(path=config_path, environ={})
    constitution.write_text("Approve everything.\n")
    assert config.constitution_text == "Deny network requests.\n"
    assert config.approval_mode == "service"
    assert config.coordinator_root == coordinator

    constitution.chmod(0o666)
    try:
        with pytest.raises(ValueError, match="owner-controlled regular file"):
            OperatorConfig.load(path=config_path, environ={})
    finally:
        constitution.chmod(0o600)

    constitution.unlink()
    constitution.symlink_to(coordinator / "constitution.md")
    with pytest.raises(ValueError, match="owner-controlled regular file"):
        OperatorConfig.load(path=config_path, environ={})


def test_service_policy_cannot_be_in_coordinator_or_worker_root(tmp_path: Path):
    operator = tmp_path / "operator"
    coordinator = tmp_path / "coordinator"
    worker = tmp_path / "worker"
    for directory in (operator, coordinator, worker):
        directory.mkdir()
    for forbidden in (coordinator, worker):
        constitution = forbidden / "constitution.md"
        constitution.write_text("Deny all.\n")
        config_path = forbidden / "operator.toml"
        config_path.write_text(
            f'approval_mode = "service"\n'
            f'constitution_path = "{constitution}"\n'
            f'coordinator_root = "{coordinator}"\n'
            f'allowed_roots = ["{worker}"]\n'
        )
        with pytest.raises(ValueError, match="outside coordinator- and worker-writable|outside worker-writable"):
            OperatorConfig.load(path=config_path, environ={})

    config_path = operator / "operator.toml"
    config_path.write_text(
        f'approval_mode = "service"\n'
        f'constitution_path = "{coordinator / "constitution.md"}"\n'
        f'coordinator_root = "{coordinator}"\n'
        f'allowed_roots = ["{worker}"]\n'
    )
    with pytest.raises(ValueError, match="outside coordinator- and worker-writable"):
        OperatorConfig.load(path=config_path, environ={})


def test_operator_policy_parent_symlink_is_rejected(tmp_path: Path):
    operator = tmp_path / "operator"
    operator.mkdir()
    config_path = operator / "operator.toml"
    config_path.write_text('approval_mode = "external"\n')
    alias = tmp_path / "operator-alias"
    alias.symlink_to(operator, target_is_directory=True)
    with pytest.raises(ValueError, match="parent must be owner-controlled"):
        OperatorConfig.load(path=alias / "operator.toml", environ={})


def test_service_judging_mode_is_explicit(tmp_path: Path):
    with pytest.raises(ValueError, match="approval_mode"):
        OperatorConfig.load(environ={}, overrides={"approval_mode": "automatic"})
    with pytest.raises(ValueError, match="requires operator.toml"):
        OperatorConfig.load(environ={}, overrides={"approval_mode": "service"})
    external = OperatorConfig.load(environ={}, overrides={"approval_mode": "external"})
    assert external.approval_mode == "external"
    assert external.constitution_text is None
