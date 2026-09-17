"""Trusted, operator-owned configuration for local coordination."""

from __future__ import annotations

import json
import math
import os
import stat
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any

from .coordinator import (
    ApprovalPolicy,
    Constitution,
    PolicyDocument,
    WorkerPermissions,
    select_worker_permissions,
)
from .daemon import default_daemon_socket
from .profiles import (
    CodexHome, PermissionProfileError, resolve_profile, verify_home,
)
from .execpolicy import ExecPolicy, ExecPolicyError


_FIELDS = frozenset({
    "allowed_roots", "permission_ceilings", "codex_command", "socket_path",
    "worker_model", "worker_reasoning_effort", "worker_allowed_reasoning_efforts",
    "worker_approval_policy",
    "allow_session_approval",
    "approval_timeout_seconds", "judge_policy", "judge_timeout_seconds",
    "event_capacity", "event_max_bytes", "item_capacity", "item_max_bytes",
    "approval_mode", "constitution_path", "coordinator_root",
    "project_constitutions", "worker_permissions", "codex_home",
})
_ENV_FIELDS = {
    "CODEX_COORDINATOR_ALLOWED_ROOTS": "allowed_roots",
    "CODEX_COORDINATOR_PERMISSION_CEILINGS": "permission_ceilings",
    "CODEX_COORDINATOR_CODEX_COMMAND": "codex_command",
    "CODEX_COORDINATOR_SOCKET_PATH": "socket_path",
    "CODEX_COORDINATOR_WORKER_MODEL": "worker_model",
    "CODEX_COORDINATOR_WORKER_REASONING_EFFORT": "worker_reasoning_effort",
    "CODEX_COORDINATOR_WORKER_ALLOWED_REASONING_EFFORTS": "worker_allowed_reasoning_efforts",
    "CODEX_COORDINATOR_WORKER_APPROVAL_POLICY": "worker_approval_policy",
    "CODEX_COORDINATOR_ALLOW_SESSION_APPROVAL": "allow_session_approval",
    "CODEX_COORDINATOR_APPROVAL_TIMEOUT_SECONDS": "approval_timeout_seconds",
    "CODEX_COORDINATOR_JUDGE_POLICY": "judge_policy",
    "CODEX_COORDINATOR_JUDGE_TIMEOUT_SECONDS": "judge_timeout_seconds",
    "CODEX_COORDINATOR_EVENT_CAPACITY": "event_capacity",
    "CODEX_COORDINATOR_EVENT_MAX_BYTES": "event_max_bytes",
    "CODEX_COORDINATOR_ITEM_CAPACITY": "item_capacity",
    "CODEX_COORDINATOR_ITEM_MAX_BYTES": "item_max_bytes",
    "CODEX_COORDINATOR_APPROVAL_MODE": "approval_mode",
    "CODEX_COORDINATOR_CONSTITUTION_PATH": "constitution_path",
    "CODEX_COORDINATOR_COORDINATOR_ROOT": "coordinator_root",
    "CODEX_COORDINATOR_PROJECT_CONSTITUTIONS": "project_constitutions",
    "CODEX_COORDINATOR_WORKER_PERMISSIONS": "worker_permissions",
    "CODEX_COORDINATOR_CODEX_HOME": "codex_home",
}


def _trusted_policy_file(path: Path, label: str) -> str:
    try:
        info = path.lstat()
        parent_info = path.parent.lstat()
    except OSError as exc:
        raise ValueError(f"{label} does not exist: {path}") from exc
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise ValueError(f"{label} must be an owner-controlled regular file")
    if not stat.S_ISDIR(parent_info.st_mode) or parent_info.st_uid != os.getuid() or parent_info.st_mode & 0o022:
        raise ValueError(f"{label} parent must be owner-controlled")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise ValueError(f"{label} must be an owner-controlled regular file") from exc
    with os.fdopen(descriptor, encoding="utf-8") as stream:
        opened = os.fstat(stream.fileno())
        if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
            raise ValueError(f"{label} changed during validation")
        return stream.read()


def _trusted_sibling_file(
    raw: Any,
    label: str,
    *,
    policy_directory: Path | None,
    forbidden_roots: tuple[Path, ...],
) -> tuple[Path, str]:
    """Validate and snapshot one operator-owned file that sits beside operator.toml.

    Trusted operator input is defined by where it lives and who owns it: beside
    the operator configuration, outside every root a coordinator or worker can
    write, owned by this user, and not reachable through a symlink.
    """
    if policy_directory is None:
        raise ValueError(f"{label} requires an operator configuration file")
    if not isinstance(raw, (str, Path)):
        raise ValueError(f"{label} must be an absolute path")
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        raise ValueError(f"{label} must be an absolute path")
    text = _trusted_policy_file(candidate, label)
    canonical = candidate.resolve(strict=True)
    if any(canonical == root or root in canonical.parents for root in forbidden_roots):
        raise ValueError(
            "trusted policy files must be outside coordinator- and worker-writable roots"
        )
    if canonical.parent != policy_directory:
        raise ValueError(f"{label} must be alongside operator.toml")
    return candidate, text


def _absolute_path(value: Any, field: str) -> Path:
    if not isinstance(value, (str, Path)) or not str(value):
        raise ValueError(f"{field} must be an absolute path")
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError(f"{field} must be an absolute path")
    return path.resolve(strict=False)


def _socket_path(value: Any) -> Path:
    if not isinstance(value, (str, Path)) or not str(value):
        raise ValueError("socket_path must be an absolute path")
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError("socket_path must be an absolute path")
    # Do not resolve the final component: validate_local_socket must detect a
    # symlink rather than silently following it.
    return path


def _positive_seconds(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a positive number")
    try:
        seconds = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a positive number") from exc
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError(f"{field} must be a positive number")
    return seconds


def _positive_count(value: Any, field: str, minimum: int = 1) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a positive integer")
    try:
        count = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a positive integer") from exc
    if str(value) != str(count) or count < minimum:
        raise ValueError(f"{field} must be an integer of at least {minimum}")
    return count


@dataclass(frozen=True)
class OperatorConfig:
    allowed_roots: tuple[Path, ...]
    permission_ceilings: Mapping[Path, Mapping[str, Any]]
    codex_command: str
    socket_path: Path
    worker_model: str | None
    worker_reasoning_effort: str
    worker_allowed_reasoning_efforts: tuple[str, ...]
    worker_approval_policy: str | None
    allow_session_approval: bool
    approval_timeout_seconds: float
    judge_policy: str
    judge_timeout_seconds: float
    event_capacity: int
    event_max_bytes: int
    item_capacity: int
    item_max_bytes: int
    approval_mode: str | None
    constitution_path: Path | None
    constitution_text: str | None
    project_constitution_paths: Mapping[Path, Path]
    constitution: Constitution | None
    coordinator_root: Path | None
    worker_permissions: Mapping[Path, WorkerPermissions]
    worker_permission_paths: Mapping[Path, Path]
    default_worker_permissions: WorkerPermissions
    codex_home: CodexHome | None

    def permissions_for(self, project: Path | str) -> WorkerPermissions:
        """The operator-declared boundary for one project, else the wide default."""
        return select_worker_permissions(
            Path(project), self.worker_permissions, self.default_worker_permissions,
        )

    @classmethod
    def load(
        cls,
        *,
        path: Path | None = None,
        environ: Mapping[str, str] | None = None,
        overrides: Mapping[str, Any] | None = None,
    ) -> "OperatorConfig":
        """Apply defaults < TOML file < environment < explicit CLI/API overrides."""
        env = os.environ if environ is None else environ
        config_path = path or (Path(env["CODEX_COORDINATOR_CONFIG"]) if env.get("CODEX_COORDINATOR_CONFIG") else None)
        values: dict[str, Any] = {
            "allowed_roots": [],
            "permission_ceilings": {},
            "codex_command": "codex",
            "socket_path": None,
            "worker_model": None,
            "worker_reasoning_effort": "low",
            "worker_allowed_reasoning_efforts": None,
            "worker_approval_policy": None,
            "allow_session_approval": False,
            "approval_timeout_seconds": 300,
            "judge_policy": "Approve only when the request is clearly safe and necessary.",
            "judge_timeout_seconds": 120,
            "event_capacity": 2048,
            "event_max_bytes": 8 * 1024 * 1024,
            "item_capacity": 256,
            "item_max_bytes": 64 * 1024,
            "approval_mode": None,
            "constitution_path": None,
            "project_constitutions": {},
            "worker_permissions": {},
            "coordinator_root": None,
            "codex_home": None,
        }
        if config_path is not None:
            config_path = Path(config_path).expanduser()
            if not config_path.is_absolute():
                raise ValueError("operator config path must be absolute")
            loaded = tomllib.loads(_trusted_policy_file(config_path, "operator config"))
            if set(loaded) - _FIELDS:
                raise ValueError(f"unsupported operator config fields: {sorted(set(loaded) - _FIELDS)}")
            values.update(loaded)
        for env_name, field_name in _ENV_FIELDS.items():
            if env_name not in env:
                continue
            raw: Any = env[env_name]
            if field_name in {
                "allowed_roots", "permission_ceilings", "project_constitutions",
                "worker_permissions", "worker_allowed_reasoning_efforts",
            }:
                try:
                    raw = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"{env_name} must contain JSON") from exc
            elif field_name == "allow_session_approval":
                if raw.lower() not in {"true", "false", "1", "0"}:
                    raise ValueError(f"{env_name} must be true or false")
                raw = raw.lower() in {"true", "1"}
            values[field_name] = raw
        if overrides:
            unknown = set(overrides) - _FIELDS
            if unknown:
                raise ValueError(f"unsupported operator overrides: {sorted(unknown)}")
            values.update({key: value for key, value in overrides.items() if value is not None})

        roots = values["allowed_roots"]
        if not isinstance(roots, (list, tuple)):
            raise ValueError("allowed_roots must be a list of absolute directories")
        canonical_roots: list[Path] = []
        for raw in roots:
            root = _absolute_path(raw, "allowed_roots entry")
            if not root.is_dir():
                raise ValueError(f"allowed root is not a directory: {root}")
            canonical_roots.append(root)
        if config_path is not None and any(
            config_path.resolve(strict=True) == root
            or root in config_path.resolve(strict=True).parents
            for root in canonical_roots
        ):
            raise ValueError("operator config must be outside worker-writable allowed roots")

        mode = values["approval_mode"]
        if mode is not None and mode not in {"service", "external"}:
            raise ValueError("approval_mode must be 'service' or 'external'")
        raw_constitution = values["constitution_path"]
        raw_project_constitutions = values["project_constitutions"]
        raw_coordinator = values["coordinator_root"]
        constitution_path = None
        constitution_text = None
        project_constitution_paths: dict[Path, Path] = {}
        constitution = None
        coordinator_root = None
        if not isinstance(raw_project_constitutions, Mapping):
            raise ValueError("project_constitutions must be a project-to-policy-file mapping")
        if raw_coordinator is not None:
            coordinator_root = _absolute_path(raw_coordinator, "coordinator_root")
            if not coordinator_root.is_dir():
                raise ValueError("coordinator_root must be an existing directory")
        if mode == "service":
            if raw_constitution is None or coordinator_root is None or config_path is None:
                raise ValueError("service judging requires operator.toml, constitution_path, and coordinator_root")
            canonical_config = config_path.resolve(strict=True)
            policy_directory = canonical_config.parent

            def _load_policy(raw: Any, label: str) -> tuple[Path, str]:
                """Validate and snapshot one operator-owned policy document."""
                candidate, text = _trusted_sibling_file(
                    raw, label,
                    policy_directory=policy_directory,
                    forbidden_roots=(*canonical_roots, coordinator_root),
                )
                if not text.strip():
                    raise ValueError(f"{label} must not be empty")
                return candidate, text

            if any(
                canonical_config == root or root in canonical_config.parents
                for root in (*canonical_roots, coordinator_root)
            ):
                raise ValueError("trusted policy files must be outside coordinator- and worker-writable roots")
            constitution_path, constitution_text = _load_policy(raw_constitution, "constitution")
            documents: dict[Path, PolicyDocument] = {}
            for raw_project, raw_policy in raw_project_constitutions.items():
                project = _absolute_path(raw_project, "project constitution project")
                if not project.is_dir() or not any(
                    project == root or root in project.parents for root in canonical_roots
                ):
                    raise ValueError(f"project constitution project is outside allowed roots: {project}")
                if project in documents:
                    raise ValueError(f"duplicate project constitution for {project}")
                policy_path, policy_text = _load_policy(
                    raw_policy, f"project constitution for {project}"
                )
                if policy_path.resolve(strict=True) == constitution_path.resolve(strict=True):
                    raise ValueError(
                        f"project constitution for {project} must not reuse the overall constitution"
                    )
                project_constitution_paths[project] = policy_path
                documents[project] = PolicyDocument(policy_text, str(policy_path))
            # Every worker must be governed by policy written for it. A shared
            # ceiling alone is the authoring failure this tier exists to remove.
            uncovered = [
                root for root in canonical_roots
                if not any(
                    project == root or project in root.parents for project in documents
                )
            ]
            if uncovered:
                raise ValueError(
                    "service judging requires a project constitution for every allowed "
                    f"root; these have none: {sorted(str(root) for root in uncovered)}"
                )
            constitution = Constitution(
                PolicyDocument(constitution_text, str(constitution_path)),
                projects=documents,
                require_project_policy=True,
            )
        else:
            if raw_constitution is not None:
                raise ValueError("constitution_path requires approval_mode = 'service'")
            if raw_project_constitutions:
                raise ValueError("project_constitutions requires approval_mode = 'service'")

        # The boundary every worker session runs under. It is read here, from
        # operator-owned files outside every worker-writable root, and never
        # from anything inside a worker project, so a worker cannot widen the
        # boundary of its own next session.
        worker_approval_policy = values["worker_approval_policy"]
        if worker_approval_policy is not None and (
            worker_approval_policy not in WorkerPermissions.WIRE_APPROVAL_POLICIES
        ):
            raise ValueError(
                "worker_approval_policy must be one of "
                f"{sorted(WorkerPermissions.WIRE_APPROVAL_POLICIES)}"
            )
        # The Codex home is where every permission profile this coordinator
        # selects is defined. It is operator-owned like operator.toml, and it is
        # read here so that an id naming nothing, a boundary wider than the one
        # this project used to enforce, or a network ceiling the runtime would
        # silently not enforce, all fail startup rather than a worker session.
        raw_codex_home = values["codex_home"]
        codex_home = None
        if raw_codex_home is not None:
            try:
                codex_home = verify_home(
                    CodexHome.load(_absolute_path(raw_codex_home, "codex_home"))
                )
            except PermissionProfileError as exc:
                raise ValueError(str(exc)) from exc
            if any(
                codex_home.path == root or root in codex_home.path.parents
                for root in (
                    *canonical_roots,
                    *(() if coordinator_root is None else (coordinator_root,)),
                )
            ):
                raise ValueError(
                    "codex_home must be outside coordinator- and worker-writable "
                    "roots; a session that can write its own permission profiles "
                    "has no boundary"
                )

        def _resolved(permissions: WorkerPermissions) -> WorkerPermissions:
            """Attach the boundary an id means, or refuse to start."""
            try:
                profile = resolve_profile(permissions.permission_profile, codex_home)
            except PermissionProfileError as exc:
                raise ValueError(f"{permissions.source}: {exc}") from exc
            return replace(permissions, profile=profile)

        default_worker_permissions = _resolved(WorkerPermissions(
            approval_policy=worker_approval_policy or WorkerPermissions().approval_policy,
            source="operator-wide default",
        ))
        raw_worker_permissions = values["worker_permissions"]
        if not isinstance(raw_worker_permissions, Mapping):
            raise ValueError("worker_permissions must be a project-to-permissions-file mapping")
        policy_directory = (
            None if config_path is None else config_path.resolve(strict=True).parent
        )
        forbidden_roots = tuple(
            root for root in (*canonical_roots, coordinator_root) if root is not None
        )
        worker_permissions: dict[Path, WorkerPermissions] = {}
        worker_permission_paths: dict[Path, Path] = {}
        for raw_project, raw_permissions in raw_worker_permissions.items():
            project = _absolute_path(raw_project, "worker permissions project")
            if not project.is_dir() or not any(
                project == root or root in project.parents for root in canonical_roots
            ):
                raise ValueError(f"worker permissions project is outside allowed roots: {project}")
            if project in worker_permissions:
                raise ValueError(f"duplicate worker permissions for {project}")
            permissions_path, permissions_text = _trusted_sibling_file(
                raw_permissions, f"worker permissions for {project}",
                policy_directory=policy_directory, forbidden_roots=forbidden_roots,
            )
            worker_permission_paths[project] = permissions_path
            permissions = WorkerPermissions.from_toml(
                permissions_text, str(permissions_path), default=default_worker_permissions,
            )
            if permissions.exec_policy_path is not None:
                # The rules file is trusted the way the permissions file is:
                # beside operator.toml, owner-controlled, no symlink, outside
                # every worker- and coordinator-writable root. A relative path
                # is taken from the permissions file's own directory, which
                # has already passed those checks. Any parse or self-check
                # failure raises here, so a policy that did not load fails
                # startup rather than quietly sending everything to a judge.
                declared = Path(permissions.exec_policy_path).expanduser()
                if not declared.is_absolute():
                    declared = permissions_path.resolve(strict=True).parent / declared
                rules_path, rules_text = _trusted_sibling_file(
                    declared, f"exec policy for {project}",
                    policy_directory=policy_directory, forbidden_roots=forbidden_roots,
                )
                try:
                    exec_policy = ExecPolicy.from_text(rules_text, source=str(rules_path))
                except ExecPolicyError as exc:
                    raise ValueError(str(exc)) from exc
                permissions = replace(permissions, exec_policy=exec_policy)
            worker_permissions[project] = _resolved(permissions)

        # An unset socket follows the configured home, so isolating the home
        # isolates the daemon with it rather than leaving the run on the shared
        # one under the developer's own ~/.codex.
        socket_path = (
            default_daemon_socket(None if codex_home is None else codex_home.path)
            if values["socket_path"] is None
            else _socket_path(values["socket_path"])
        )

        raw_ceilings = values["permission_ceilings"]
        if not isinstance(raw_ceilings, Mapping):
            raise ValueError("permission_ceilings must be a project-to-permissions mapping")
        ceilings: dict[Path, Mapping[str, Any]] = {}
        for raw_project, raw_ceiling in raw_ceilings.items():
            project = _absolute_path(raw_project, "permission ceiling project")
            if not project.is_dir() or not any(
                project == root or root in project.parents for root in canonical_roots
            ):
                raise ValueError(f"permission ceiling project is outside allowed roots: {project}")
            if not isinstance(raw_ceiling, Mapping):
                raise ValueError("permission ceiling must be a mapping")
            policy = ApprovalPolicy(project, allowed_permissions=raw_ceiling)
            ceilings[project] = policy.allowed_permissions

        for field_name in ("codex_command", "worker_reasoning_effort", "judge_policy"):
            if not isinstance(values[field_name], str) or not values[field_name].strip():
                raise ValueError(f"{field_name} must be a nonempty string")
        model = values["worker_model"]
        if model is not None and (not isinstance(model, str) or not model.strip()):
            raise ValueError("worker_model must be a nonempty string or omitted")
        if not isinstance(values["allow_session_approval"], bool):
            raise ValueError("allow_session_approval must be a boolean")
        raw_efforts = values["worker_allowed_reasoning_efforts"]
        if raw_efforts is None:
            worker_allowed_reasoning_efforts = (values["worker_reasoning_effort"],)
        else:
            if (
                not isinstance(raw_efforts, (list, tuple))
                or not raw_efforts
                or any(
                    not isinstance(item, str) or not item.strip()
                    for item in raw_efforts
                )
            ):
                raise ValueError(
                    "worker_allowed_reasoning_efforts must be a nonempty list of strings"
                )
            worker_allowed_reasoning_efforts = tuple(dict.fromkeys(raw_efforts))
            if values["worker_reasoning_effort"] not in worker_allowed_reasoning_efforts:
                raise ValueError(
                    "worker_reasoning_effort must be included in "
                    "worker_allowed_reasoning_efforts"
                )
        return cls(
            allowed_roots=tuple(canonical_roots),
            permission_ceilings=MappingProxyType(ceilings),
            codex_command=values["codex_command"],
            socket_path=socket_path,
            worker_model=model,
            worker_reasoning_effort=values["worker_reasoning_effort"],
            worker_allowed_reasoning_efforts=worker_allowed_reasoning_efforts,
            worker_approval_policy=worker_approval_policy,
            allow_session_approval=values["allow_session_approval"],
            approval_timeout_seconds=_positive_seconds(
                values["approval_timeout_seconds"], "approval_timeout_seconds"
            ),
            judge_policy=values["judge_policy"],
            judge_timeout_seconds=_positive_seconds(
                values["judge_timeout_seconds"], "judge_timeout_seconds"
            ),
            event_capacity=_positive_count(values["event_capacity"], "event_capacity"),
            event_max_bytes=_positive_count(values["event_max_bytes"], "event_max_bytes", 256),
            item_capacity=_positive_count(values["item_capacity"], "item_capacity"),
            item_max_bytes=_positive_count(values["item_max_bytes"], "item_max_bytes", 256),
            approval_mode=mode,
            constitution_path=constitution_path,
            constitution_text=constitution_text,
            project_constitution_paths=MappingProxyType(project_constitution_paths),
            constitution=constitution,
            coordinator_root=coordinator_root,
            worker_permissions=MappingProxyType(worker_permissions),
            worker_permission_paths=MappingProxyType(worker_permission_paths),
            default_worker_permissions=default_worker_permissions,
            codex_home=codex_home,
        )
