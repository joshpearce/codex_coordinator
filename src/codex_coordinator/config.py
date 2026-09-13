"""Trusted, operator-owned configuration for local coordination."""

from __future__ import annotations

import json
import math
import os
import stat
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from .coordinator import ApprovalPolicy
from .daemon import default_daemon_socket


_FIELDS = frozenset({
    "allowed_roots", "permission_ceilings", "codex_command", "socket_path",
    "worker_model", "worker_reasoning_effort", "allow_session_approval",
    "approval_timeout_seconds", "judge_policy", "judge_timeout_seconds",
    "event_capacity", "event_max_bytes", "item_capacity", "item_max_bytes",
})
_ENV_FIELDS = {
    "CODEX_COORDINATOR_ALLOWED_ROOTS": "allowed_roots",
    "CODEX_COORDINATOR_PERMISSION_CEILINGS": "permission_ceilings",
    "CODEX_COORDINATOR_CODEX_COMMAND": "codex_command",
    "CODEX_COORDINATOR_SOCKET_PATH": "socket_path",
    "CODEX_COORDINATOR_WORKER_MODEL": "worker_model",
    "CODEX_COORDINATOR_WORKER_REASONING_EFFORT": "worker_reasoning_effort",
    "CODEX_COORDINATOR_ALLOW_SESSION_APPROVAL": "allow_session_approval",
    "CODEX_COORDINATOR_APPROVAL_TIMEOUT_SECONDS": "approval_timeout_seconds",
    "CODEX_COORDINATOR_JUDGE_POLICY": "judge_policy",
    "CODEX_COORDINATOR_JUDGE_TIMEOUT_SECONDS": "judge_timeout_seconds",
    "CODEX_COORDINATOR_EVENT_CAPACITY": "event_capacity",
    "CODEX_COORDINATOR_EVENT_MAX_BYTES": "event_max_bytes",
    "CODEX_COORDINATOR_ITEM_CAPACITY": "item_capacity",
    "CODEX_COORDINATOR_ITEM_MAX_BYTES": "item_max_bytes",
}


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
    allow_session_approval: bool
    approval_timeout_seconds: float
    judge_policy: str
    judge_timeout_seconds: float
    event_capacity: int
    event_max_bytes: int
    item_capacity: int
    item_max_bytes: int

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
            "socket_path": str(default_daemon_socket()),
            "worker_model": None,
            "worker_reasoning_effort": "low",
            "allow_session_approval": False,
            "approval_timeout_seconds": 300,
            "judge_policy": "Approve only when the request is clearly safe and necessary.",
            "judge_timeout_seconds": 120,
            "event_capacity": 2048,
            "event_max_bytes": 8 * 1024 * 1024,
            "item_capacity": 256,
            "item_max_bytes": 64 * 1024,
        }
        if config_path is not None:
            config_path = Path(config_path).expanduser()
            if not config_path.is_absolute():
                raise ValueError("operator config path must be absolute")
            info = config_path.lstat()
            parent_info = config_path.parent.stat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
                raise ValueError("operator config must be an owner-controlled regular file")
            if parent_info.st_uid != os.getuid() or parent_info.st_mode & 0o022:
                raise ValueError("operator config parent must be owner-controlled")
            with config_path.open("rb") as stream:
                loaded = tomllib.load(stream)
            if set(loaded) - _FIELDS:
                raise ValueError(f"unsupported operator config fields: {sorted(set(loaded) - _FIELDS)}")
            values.update(loaded)
        for env_name, field_name in _ENV_FIELDS.items():
            if env_name not in env:
                continue
            raw: Any = env[env_name]
            if field_name in {"allowed_roots", "permission_ceilings"}:
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
        if config_path is not None:
            canonical_config = config_path.resolve(strict=True)
            if any(
                canonical_config == root or root in canonical_config.parents
                for root in canonical_roots
            ):
                raise ValueError("operator config must be outside worker-writable allowed roots")

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
        return cls(
            allowed_roots=tuple(canonical_roots),
            permission_ceilings=MappingProxyType(ceilings),
            codex_command=values["codex_command"],
            socket_path=_socket_path(values["socket_path"]),
            worker_model=model,
            worker_reasoning_effort=values["worker_reasoning_effort"],
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
        )
