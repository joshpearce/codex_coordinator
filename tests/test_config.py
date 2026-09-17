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
        'worker_allowed_reasoning_efforts = ["low", "medium"]\n'
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
    assert config.worker_allowed_reasoning_efforts == ("low", "medium")
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
    (operator / "worker.md").write_text("This worker runs its own tests.\n")
    config_path = operator / "operator.toml"
    config_path.write_text(
        f'approval_mode = "service"\n'
        f'constitution_path = "{constitution}"\n'
        f'coordinator_root = "{coordinator}"\n'
        f'allowed_roots = ["{worker}"]\n'
        "[project_constitutions]\n"
        f'"{worker}" = "{operator / "worker.md"}"\n'
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
            "[project_constitutions]\n"
            f'"{worker}" = "{forbidden / "worker.md"}"\n'
        )
        with pytest.raises(ValueError, match="outside coordinator- and worker-writable|outside worker-writable"):
            OperatorConfig.load(path=config_path, environ={})

    config_path = operator / "operator.toml"
    config_path.write_text(
        f'approval_mode = "service"\n'
        f'constitution_path = "{coordinator / "constitution.md"}"\n'
        f'coordinator_root = "{coordinator}"\n'
        f'allowed_roots = ["{worker}"]\n'
        "[project_constitutions]\n"
        f'"{worker}" = "{operator / "worker.md"}"\n'
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


def _two_tier_workspace(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    operator = tmp_path / "operator"
    coordinator = tmp_path / "coordinator"
    mail = tmp_path / "mail"
    web = tmp_path / "web"
    for directory in (operator, coordinator, mail, web):
        directory.mkdir()
    (operator / "constitution.md").write_text("No worker may use the network.\n")
    (operator / "mail.md").write_text("This worker reads mail and never sends.\n")
    (operator / "web.md").write_text("This worker may search the web.\n")
    return operator, coordinator, mail, web


def _two_tier_config(operator: Path, coordinator: Path, mail: Path, web: Path, *, projects: str | None = None) -> Path:
    config_path = operator / "operator.toml"
    body = (
        'approval_mode = "service"\n'
        f'constitution_path = "{operator / "constitution.md"}"\n'
        f'coordinator_root = "{coordinator}"\n'
        f'allowed_roots = ["{mail}", "{web}"]\n'
    )
    body += projects if projects is not None else (
        "[project_constitutions]\n"
        f'"{mail}" = "{operator / "mail.md"}"\n'
        f'"{web}" = "{operator / "web.md"}"\n'
    )
    config_path.write_text(body)
    return config_path


def test_per_project_constitutions_are_snapshotted_and_project_scoped(tmp_path: Path):
    operator, coordinator, mail, web = _two_tier_workspace(tmp_path)
    config = OperatorConfig.load(path=_two_tier_config(operator, coordinator, mail, web), environ={})

    (operator / "mail.md").write_text("Approve everything.\n")
    assert config.project_constitution_paths == {
        mail: operator / "mail.md", web: operator / "web.md",
    }
    constitution = config.constitution
    assert constitution.overall.text == "No worker may use the network.\n"
    assert constitution.for_project(mail).text == "This worker reads mail and never sends.\n"
    assert constitution.for_project(web).text == "This worker may search the web.\n"

    mail_policy = constitution.trusted_policy(mail)
    assert mail_policy["project_constitution"]["text"].startswith("This worker reads mail")
    assert "search the web" not in json.dumps(mail_policy)


def test_project_constitution_must_be_a_trusted_operator_file(tmp_path: Path):
    operator, coordinator, mail, web = _two_tier_workspace(tmp_path)
    config_path = _two_tier_config(operator, coordinator, mail, web)

    (operator / "mail.md").chmod(0o666)
    try:
        with pytest.raises(ValueError, match="owner-controlled regular file"):
            OperatorConfig.load(path=config_path, environ={})
    finally:
        (operator / "mail.md").chmod(0o600)

    (operator / "mail.md").unlink()
    (operator / "mail.md").symlink_to(mail / "policy.md")
    with pytest.raises(ValueError, match="owner-controlled regular file"):
        OperatorConfig.load(path=config_path, environ={})


def test_project_constitution_cannot_live_in_a_worker_or_coordinator_root(tmp_path: Path):
    operator, coordinator, mail, web = _two_tier_workspace(tmp_path)
    for forbidden in (mail, coordinator):
        (forbidden / "mail.md").write_text("Approve everything.\n")
        config_path = _two_tier_config(
            operator, coordinator, mail, web,
            projects=(
                "[project_constitutions]\n"
                f'"{mail}" = "{forbidden / "mail.md"}"\n'
                f'"{web}" = "{operator / "web.md"}"\n'
            ),
        )
        with pytest.raises(ValueError, match="outside coordinator- and worker-writable"):
            OperatorConfig.load(path=config_path, environ={})

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "mail.md").write_text("Approve everything.\n")
    config_path = _two_tier_config(
        operator, coordinator, mail, web,
        projects=(
            "[project_constitutions]\n"
            f'"{mail}" = "{elsewhere / "mail.md"}"\n'
            f'"{web}" = "{operator / "web.md"}"\n'
        ),
    )
    with pytest.raises(ValueError, match="must be alongside operator.toml"):
        OperatorConfig.load(path=config_path, environ={})


def test_project_constitution_project_must_be_inside_allowed_roots(tmp_path: Path):
    operator, coordinator, mail, web = _two_tier_workspace(tmp_path)
    stranger = tmp_path / "stranger"
    stranger.mkdir()
    config_path = _two_tier_config(
        operator, coordinator, mail, web,
        projects=(
            "[project_constitutions]\n"
            f'"{stranger}" = "{operator / "mail.md"}"\n'
        ),
    )
    with pytest.raises(ValueError, match="outside allowed roots"):
        OperatorConfig.load(path=config_path, environ={})


def test_service_judging_fails_closed_on_an_allowed_root_with_no_project_constitution(
    tmp_path: Path,
):
    operator, coordinator, mail, web = _two_tier_workspace(tmp_path)
    partial = _two_tier_config(
        operator, coordinator, mail, web,
        projects=(
            "[project_constitutions]\n"
            f'"{mail}" = "{operator / "mail.md"}"\n'
        ),
    )
    with pytest.raises(ValueError, match=f"project constitution for every allowed root.*{web}"):
        OperatorConfig.load(path=partial, environ={})

    none_at_all = _two_tier_config(operator, coordinator, mail, web, projects="")
    with pytest.raises(ValueError, match="project constitution for every allowed root"):
        OperatorConfig.load(path=none_at_all, environ={})

    # A root covered at the root itself governs every project beneath it.
    nested = mail / "service-a"
    nested.mkdir()
    config = OperatorConfig.load(
        path=_two_tier_config(operator, coordinator, mail, web), environ={}
    )
    assert config.constitution.for_project(nested).source == str(operator / "mail.md")
    assert config.constitution.require_project_policy
    assert config.constitution.covers(nested)


def test_project_constitution_must_differ_from_the_overall_constitution(tmp_path: Path):
    operator, coordinator, mail, web = _two_tier_workspace(tmp_path)
    config_path = _two_tier_config(
        operator, coordinator, mail, web,
        projects=(
            "[project_constitutions]\n"
            f'"{mail}" = "{operator / "constitution.md"}"\n'
        ),
    )
    with pytest.raises(ValueError, match="must not reuse the overall constitution"):
        OperatorConfig.load(path=config_path, environ={})


def test_two_tier_settings_require_service_judging(tmp_path: Path):
    operator, coordinator, mail, web = _two_tier_workspace(tmp_path)
    config_path = operator / "operator.toml"
    config_path.write_text(
        'approval_mode = "external"\n'
        f'allowed_roots = ["{mail}"]\n'
        "[project_constitutions]\n"
        f'"{mail}" = "{operator / "mail.md"}"\n'
    )
    with pytest.raises(ValueError, match="project_constitutions requires approval_mode"):
        OperatorConfig.load(path=config_path, environ={})



def test_worker_approval_policy_is_operator_owned_and_validated(tmp_path: Path):
    """The operator, not the worker's own file, decides how much is judged."""
    assert OperatorConfig.load(environ={}, overrides={}).worker_approval_policy is None

    for policy in ("on-request", "untrusted"):
        config = OperatorConfig.load(
            environ={}, overrides={"worker_approval_policy": policy}
        )
        assert config.worker_approval_policy == policy

    # "never" would execute unjudged; anything else is not a wire value the
    # pinned app-server accepts.
    for policy in ("never", "on-failure", "", "auto"):
        with pytest.raises(ValueError, match="worker_approval_policy must be one of"):
            OperatorConfig.load(environ={}, overrides={"worker_approval_policy": policy})

    from_env = OperatorConfig.load(
        environ={"CODEX_COORDINATOR_WORKER_APPROVAL_POLICY": "untrusted"}
    )
    assert from_env.worker_approval_policy == "untrusted"

    root = tmp_path / "worker"
    root.mkdir()
    config_path = tmp_path / "operator.toml"
    config_path.write_text(
        f'allowed_roots = ["{root}"]\nworker_approval_policy = "untrusted"\n'
    )
    assert OperatorConfig.load(
        path=config_path, environ={}
    ).worker_approval_policy == "untrusted"


def _operator_with_permissions(tmp_path: Path, permissions: str, *, extra: str = "") -> Path:
    """An operator configuration that declares one worker's boundary."""
    worker = tmp_path / "worker"
    worker.mkdir(exist_ok=True)
    (tmp_path / "worker.permissions.toml").write_text(permissions)
    config_path = tmp_path / "operator.toml"
    config_path.write_text(
        f'allowed_roots = ["{worker}"]\n'
        f"{extra}"
        "[worker_permissions]\n"
        f'"{worker}" = "{tmp_path / "worker.permissions.toml"}"\n'
    )
    return config_path


def test_worker_permissions_come_from_an_operator_owned_file(tmp_path: Path):
    """The boundary is declared outside the root it governs, so the worker cannot edit it."""
    config_path = _operator_with_permissions(
        tmp_path,
        "# The boundary for this worker.\n"
        'approval_policy = "untrusted"\n'
        'approvals_reviewer = "user"\n'
        'permission_profile = ":read-only"\n',
    )
    config = OperatorConfig.load(path=config_path, environ={})
    worker = tmp_path / "worker"
    permissions = config.permissions_for(worker)
    assert permissions.approval_policy == "untrusted"
    assert permissions.permission_profile == ":read-only"
    assert permissions.source == str(tmp_path / "worker.permissions.toml")
    assert config.worker_permission_paths == {worker: tmp_path / "worker.permissions.toml"}
    # A worker file inside the project is not an input, whatever it says.
    (worker / ".codex").mkdir()
    (worker / ".codex/config.toml").write_text('permission_profile = ":danger-full-access"\n')
    assert OperatorConfig.load(
        path=config_path, environ={},
    ).permissions_for(worker).permission_profile == ":read-only"
    # An undeclared project falls back to the operator-wide default.
    assert config.permissions_for(tmp_path / "elsewhere").source == "operator-wide default"


def test_worker_permissions_default_to_the_operator_wide_approval_policy(tmp_path: Path):
    config_path = _operator_with_permissions(
        tmp_path, 'permission_profile = ":read-only"\n',
        extra='worker_approval_policy = "untrusted"\n',
    )
    permissions = OperatorConfig.load(
        path=config_path, environ={},
    ).permissions_for(tmp_path / "worker")
    assert permissions.approval_policy == "untrusted"
    assert permissions.approvals_reviewer == "user"
    assert permissions.permission_profile == ":read-only"


def test_worker_permissions_file_must_be_a_trusted_operator_file(tmp_path: Path):
    worker = tmp_path / "worker"
    worker.mkdir()
    inside = worker / "permissions.toml"
    inside.write_text('permission_profile = "worker_workspace"\n')
    config_path = tmp_path / "operator.toml"

    def load(declared: Path) -> None:
        config_path.write_text(
            f'allowed_roots = ["{worker}"]\n'
            "[worker_permissions]\n"
            f'"{worker}" = "{declared}"\n'
        )
        OperatorConfig.load(path=config_path, environ={})

    # Inside the root it governs: the worker could rewrite its own boundary.
    with pytest.raises(ValueError, match="outside coordinator- and worker-writable roots"):
        load(inside)

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    sibling = elsewhere / "permissions.toml"
    sibling.write_text('permission_profile = "worker_workspace"\n')
    with pytest.raises(ValueError, match="must be alongside operator.toml"):
        load(sibling)

    writable = tmp_path / "writable.permissions.toml"
    writable.write_text('permission_profile = "worker_workspace"\n')
    writable.chmod(0o666)
    with pytest.raises(ValueError, match="owner-controlled regular file"):
        load(writable)
    writable.chmod(0o600)

    link = tmp_path / "linked.permissions.toml"
    link.symlink_to(sibling)
    with pytest.raises(ValueError, match="owner-controlled regular file"):
        load(link)

    with pytest.raises(ValueError, match="must be an absolute path"):
        load(Path("relative.permissions.toml"))


def test_worker_permissions_project_must_be_inside_allowed_roots(tmp_path: Path):
    worker = tmp_path / "worker"
    outside = tmp_path / "outside"
    for directory in (worker, outside):
        directory.mkdir()
    (tmp_path / "worker.permissions.toml").write_text('permission_profile = ":read-only"\n')
    config_path = tmp_path / "operator.toml"
    config_path.write_text(
        f'allowed_roots = ["{worker}"]\n'
        "[worker_permissions]\n"
        f'"{outside}" = "{tmp_path / "worker.permissions.toml"}"\n'
    )
    with pytest.raises(ValueError, match="worker permissions project is outside allowed roots"):
        OperatorConfig.load(path=config_path, environ={})


def test_worker_permissions_require_an_operator_configuration_file(tmp_path: Path):
    worker = tmp_path / "worker"
    worker.mkdir()
    (tmp_path / "worker.permissions.toml").write_text('permission_profile = ":read-only"\n')
    with pytest.raises(ValueError, match="requires an operator configuration file"):
        OperatorConfig.load(environ={}, overrides={
            "allowed_roots": [str(worker)],
            "worker_permissions": {str(worker): str(tmp_path / "worker.permissions.toml")},
        })


def _rules(text: str = 'prefix_rule(pattern=["sed", "-n"], decision="allow", justification="reads")\n') -> str:
    return text


def _exec_policy_config(tmp_path: Path, *, rules_reference: str, rules_text: str | None = _rules()):
    worker = tmp_path / "worker"
    worker.mkdir(exist_ok=True)
    if rules_text is not None:
        (tmp_path / "worker.rules").write_text(rules_text)
    (tmp_path / "worker.permissions.toml").write_text(
        'approval_policy = "untrusted"\n'
        f'exec_policy = "{rules_reference}"\n'
    )
    config_path = tmp_path / "operator.toml"
    config_path.write_text(
        f'allowed_roots = ["{worker}"]\n'
        "[worker_permissions]\n"
        f'"{worker}" = "{tmp_path / "worker.permissions.toml"}"\n'
    )
    return worker, config_path


def test_exec_policy_is_loaded_from_a_trusted_sibling_of_the_permissions_file(tmp_path: Path):
    """A relative reference resolves beside the permissions file; an absolute one must land there too."""
    for reference in ("worker.rules", str(tmp_path / "worker.rules")):
        worker, config_path = _exec_policy_config(tmp_path, rules_reference=reference)
        config = OperatorConfig.load(path=config_path, environ={})
        permissions = config.permissions_for(worker)
        assert permissions.exec_policy_path == reference
        assert permissions.exec_policy is not None
        assert permissions.exec_policy.source == str(tmp_path / "worker.rules")
        assert permissions.exec_policy_loaded
        assert permissions.provenance()["execPolicy"] == {
            "source": str(tmp_path / "worker.rules"),
            "digest": permissions.exec_policy.digest,
            "rules": 1,
            "escalations": 0,
        }
        # The rules are a per-project statement: the operator-wide default
        # never inherits one.
        assert config.default_worker_permissions.exec_policy is None
        assert config.default_worker_permissions.exec_policy_path is None


def test_exec_policy_that_does_not_load_fails_startup(tmp_path: Path):
    """Loading fails closed: a rules file the coordinator cannot accept is a startup error."""
    _worker, config_path = _exec_policy_config(tmp_path, rules_reference="worker.rules", rules_text=None)
    with pytest.raises(ValueError, match="exec policy for .* does not exist"):
        OperatorConfig.load(path=config_path, environ={})

    _worker, config_path = _exec_policy_config(
        tmp_path, rules_reference="worker.rules",
        rules_text='prefix_rule(pattern=["sed"], decision="forbidden", justification="x")\n',
    )
    with pytest.raises(ValueError, match='decision must be "allow" for a command rule'):
        OperatorConfig.load(path=config_path, environ={})

    _worker, config_path = _exec_policy_config(
        tmp_path, rules_reference="worker.rules",
        rules_text='prefix_rule(pattern=["sed"], decision="allow", justification="x", match=[["cat", "x"]])\n',
    )
    with pytest.raises(ValueError, match="match example is not allowed"):
        OperatorConfig.load(path=config_path, environ={})

    _worker, config_path = _exec_policy_config(tmp_path, rules_reference="")
    with pytest.raises(ValueError, match="exec_policy must be a nonempty path"):
        OperatorConfig.load(path=config_path, environ={})


def test_exec_policy_must_be_a_trusted_operator_file(tmp_path: Path):
    """The rules file obeys the same ownership and location rules as a constitution."""
    worker, config_path = _exec_policy_config(tmp_path, rules_reference="worker.rules")

    # Inside the worker's own writable root: exactly the file a worker could
    # rewrite to allow itself anything.
    inside = worker / "worker.rules"
    inside.write_text(_rules())
    (tmp_path / "worker.permissions.toml").write_text(f'exec_policy = "{inside}"\n')
    with pytest.raises(ValueError, match="outside coordinator- and worker-writable roots"):
        OperatorConfig.load(path=config_path, environ={})

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "worker.rules").write_text(_rules())
    (tmp_path / "worker.permissions.toml").write_text(f'exec_policy = "{elsewhere / "worker.rules"}"\n')
    with pytest.raises(ValueError, match="must be alongside operator.toml"):
        OperatorConfig.load(path=config_path, environ={})

    (tmp_path / "worker.permissions.toml").write_text('exec_policy = "../elsewhere/worker.rules"\n')
    with pytest.raises(ValueError, match="does not exist|must be alongside operator.toml"):
        OperatorConfig.load(path=config_path, environ={})
    (tmp_path.parent / "elsewhere").mkdir(exist_ok=True)
    (tmp_path.parent / "elsewhere/worker.rules").write_text(_rules())
    with pytest.raises(ValueError, match="must be alongside operator.toml"):
        OperatorConfig.load(path=config_path, environ={})

    writable = tmp_path / "writable.rules"
    writable.write_text(_rules())
    writable.chmod(0o666)
    (tmp_path / "worker.permissions.toml").write_text('exec_policy = "writable.rules"\n')
    with pytest.raises(ValueError, match="owner-controlled regular file"):
        OperatorConfig.load(path=config_path, environ={})

    link = tmp_path / "linked.rules"
    link.symlink_to(tmp_path / "worker.rules")
    (tmp_path / "worker.permissions.toml").write_text('exec_policy = "linked.rules"\n')
    with pytest.raises(ValueError, match="owner-controlled regular file"):
        OperatorConfig.load(path=config_path, environ={})

    # A worker project's own `.codex/rules` is never consulted by the loader,
    # whatever it says.
    (worker / ".codex/rules").mkdir(parents=True)
    (worker / ".codex/rules/worker.rules").write_text(
        'prefix_rule(pattern=["rm"], decision="allow", justification="worker-authored")\n'
    )
    (tmp_path / "worker.permissions.toml").write_text('exec_policy = "worker.rules"\n')
    config = OperatorConfig.load(path=config_path, environ={})
    policy = config.permissions_for(worker).exec_policy
    assert policy is not None
    assert [rule.pattern[0] for rule in policy.rules] == [("sed",)]


def test_codex_home_defines_the_profiles_and_is_resolved_at_startup(tmp_path: Path):
    """An id that names nothing fails startup, not a worker session (#0021)."""
    worker = tmp_path / "worker"
    worker.mkdir()
    home = tmp_path / "codex-home"
    home.mkdir()
    (home / "config.toml").write_text(
        'default_permissions = ":read-only"\n'
        "\n"
        "[permissions.worker_workspace]\n"
        'extends = ":workspace"\n'
        'filesystem = { ":tmpdir" = "read", ":slash_tmp" = "read" }\n'
    )
    (tmp_path / "worker.permissions.toml").write_text(
        'permission_profile = "worker_workspace"\n'
    )
    config_path = tmp_path / "operator.toml"

    def write(profile: str, codex_home: Path | None = home) -> None:
        (tmp_path / "worker.permissions.toml").write_text(
            f'permission_profile = "{profile}"\n'
        )
        config_path.write_text(
            f'allowed_roots = ["{worker}"]\n'
            + (f'codex_home = "{codex_home}"\n' if codex_home is not None else "")
            + "[worker_permissions]\n"
            f'"{worker}" = "{tmp_path / "worker.permissions.toml"}"\n'
        )

    write("worker_workspace")
    config = OperatorConfig.load(path=config_path, environ={})
    permissions = config.permissions_for(worker)
    assert config.codex_home is not None and config.codex_home.path == home
    assert permissions.profile is not None
    assert permissions.profile.chain == ("worker_workspace", ":workspace")
    assert permissions.writable
    # An unset socket follows the home, so isolating one isolates the daemon.
    assert config.socket_path.parent.parent == home

    write("absent_profile")
    with pytest.raises(ValueError, match="is not defined in"):
        OperatorConfig.load(path=config_path, environ={})

    # Without a home only the built-ins resolve, and none of them is quietly
    # taken from the developer's own ~/.codex.
    write("worker_workspace", codex_home=None)
    with pytest.raises(ValueError, match="no codex_home is configured"):
        OperatorConfig.load(path=config_path, environ={})
    write(":read-only", codex_home=None)
    assert OperatorConfig.load(path=config_path, environ={}).codex_home is None


def test_codex_home_must_be_outside_every_writable_root(tmp_path: Path):
    """A session that can write its own permission profile has no boundary."""
    worker = tmp_path / "worker"
    inside = worker / "codex-home"
    inside.mkdir(parents=True)
    (inside / "config.toml").write_text('default_permissions = ":read-only"\n')
    config_path = tmp_path / "operator.toml"
    config_path.write_text(
        f'allowed_roots = ["{worker}"]\n'
        f'codex_home = "{inside}"\n'
    )

    with pytest.raises(ValueError, match="codex_home must be outside"):
        OperatorConfig.load(path=config_path, environ={})


def test_a_home_with_an_inert_network_ceiling_fails_startup(tmp_path: Path):
    """The live gate's own home would fail this if the feature were turned off.

    The worker profile a project selects declares no network grants at all, so
    scoping this to the selected profile would let a home full of inert ceilings
    start cleanly. It is the home that is misconfigured.
    """
    worker = tmp_path / "worker"
    worker.mkdir()
    home = tmp_path / "codex-home"
    home.mkdir()
    profiles = (
        "[permissions.worker_workspace]\n"
        'extends = ":workspace"\n'
        'filesystem = { ":tmpdir" = "read", ":slash_tmp" = "read" }\n'
        "\n"
        "[permissions.worker_pypi]\n"
        'extends = ":workspace"\n'
        'filesystem = { ":tmpdir" = "read", ":slash_tmp" = "read" }\n'
        "\n"
        "[permissions.worker_pypi.network]\n"
        "enabled = true\n"
        'mode = "limited"\n'
        'domains = { "example.com" = "allow" }\n'
    )
    (tmp_path / "worker.permissions.toml").write_text(
        'permission_profile = "worker_workspace"\n'
    )
    config_path = tmp_path / "operator.toml"
    config_path.write_text(
        f'allowed_roots = ["{worker}"]\n'
        f'codex_home = "{home}"\n'
        "[worker_permissions]\n"
        f'"{worker}" = "{tmp_path / "worker.permissions.toml"}"\n'
    )

    (home / "config.toml").write_text('default_permissions = ":read-only"\n\n' + profiles)
    with pytest.raises(ValueError, match="network_proxy"):
        OperatorConfig.load(path=config_path, environ={})

    (home / "config.toml").write_text(
        'default_permissions = ":read-only"\n\n[features]\nnetwork_proxy = true\n\n' + profiles
    )
    config = OperatorConfig.load(path=config_path, environ={})
    assert config.codex_home is not None and config.codex_home.network_proxy
