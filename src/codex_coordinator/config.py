"""Configuration for transparent local Codex session orchestration."""

from __future__ import annotations

import json
import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from .daemon import default_daemon_socket

_FIELDS = frozenset({"projects", "codex_command", "socket_path", "worker_model", "worker_reasoning_effort", "event_capacity", "event_max_bytes", "item_capacity", "item_max_bytes"})
_ENV_FIELDS = {
    "CODEX_COORDINATOR_PROJECTS": "projects",
    "CODEX_COORDINATOR_CODEX_COMMAND": "codex_command",
    "CODEX_COORDINATOR_SOCKET_PATH": "socket_path",
    "CODEX_COORDINATOR_WORKER_MODEL": "worker_model",
    "CODEX_COORDINATOR_WORKER_REASONING_EFFORT": "worker_reasoning_effort",
    "CODEX_COORDINATOR_EVENT_CAPACITY": "event_capacity",
    "CODEX_COORDINATOR_EVENT_MAX_BYTES": "event_max_bytes",
    "CODEX_COORDINATOR_ITEM_CAPACITY": "item_capacity",
    "CODEX_COORDINATOR_ITEM_MAX_BYTES": "item_max_bytes",
}


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


def _socket_path(value: Any) -> Path:
    if not isinstance(value, (str, Path)) or not str(value):
        raise ValueError("socket_path must be an absolute path")
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError("socket_path must be an absolute path")
    return path


def _projects(value: Any) -> Mapping[str, Path]:
    if not isinstance(value, Mapping):
        raise ValueError("projects must be a name-to-absolute-path mapping")
    projects: dict[str, Path] = {}
    for name, raw_path in value.items():
        if not isinstance(name, str) or not name.strip() or name != name.strip():
            raise ValueError("project names must be nonempty text without surrounding whitespace")
        if name in projects:
            raise ValueError(f"duplicate project name: {name}")
        if not isinstance(raw_path, (str, Path)) or not str(raw_path):
            raise ValueError(f"project {name!r} must name an absolute path")
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            raise ValueError(f"project {name!r} path must be absolute")
        try:
            canonical = path.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ValueError(f"project {name!r} path is unavailable: {path}") from exc
        if not canonical.is_dir():
            raise ValueError(f"project {name!r} path is not a directory: {canonical}")
        projects[name] = canonical
    return MappingProxyType(projects)


@dataclass(frozen=True)
class OperatorConfig:
    projects: Mapping[str, Path]
    codex_command: str
    socket_path: Path
    worker_model: str | None
    worker_reasoning_effort: str | None
    event_capacity: int
    event_max_bytes: int
    item_capacity: int
    item_max_bytes: int

    @classmethod
    def load(cls, *, path: Path | None = None, environ: Mapping[str, str] | None = None, overrides: Mapping[str, Any] | None = None) -> "OperatorConfig":
        """Apply defaults < TOML file < environment < explicit overrides."""
        env = os.environ if environ is None else environ
        config_path = path or (Path(env["CODEX_COORDINATOR_CONFIG"]) if env.get("CODEX_COORDINATOR_CONFIG") else None)
        values: dict[str, Any] = {
            "projects": {}, "codex_command": "codex", "socket_path": None,
            "worker_model": None, "worker_reasoning_effort": None,
            "event_capacity": 2048, "event_max_bytes": 8 * 1024 * 1024,
            "item_capacity": 256, "item_max_bytes": 64 * 1024,
        }
        if config_path is not None:
            config_path = Path(config_path).expanduser()
            if not config_path.is_absolute():
                raise ValueError("operator config path must be absolute")
            try:
                loaded = tomllib.loads(config_path.read_text())
            except (OSError, tomllib.TOMLDecodeError) as exc:
                raise ValueError(f"cannot read operator config {config_path}: {exc}") from exc
            unknown = set(loaded) - _FIELDS
            if unknown:
                raise ValueError(f"unsupported operator config fields: {sorted(unknown)}")
            values.update(loaded)
        for env_name, field_name in _ENV_FIELDS.items():
            if env_name not in env:
                continue
            raw: Any = env[env_name]
            if field_name == "projects":
                try:
                    raw = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"{env_name} must contain JSON") from exc
            values[field_name] = raw
        if overrides:
            unknown = set(overrides) - _FIELDS
            if unknown:
                raise ValueError(f"unsupported operator overrides: {sorted(unknown)}")
            values.update({key: value for key, value in overrides.items() if value is not None})
        codex_command = values["codex_command"]
        if not isinstance(codex_command, str) or not codex_command.strip():
            raise ValueError("codex_command must be nonempty text")
        model, effort = values["worker_model"], values["worker_reasoning_effort"]
        if model is not None and (not isinstance(model, str) or not model.strip()):
            raise ValueError("worker_model must be nonempty text when set")
        if effort is not None and (not isinstance(effort, str) or not effort.strip()):
            raise ValueError("worker_reasoning_effort must be nonempty text when set")
        return cls(
            projects=_projects(values["projects"]), codex_command=codex_command,
            socket_path=_socket_path(values["socket_path"]) if values["socket_path"] is not None else default_daemon_socket(),
            worker_model=model, worker_reasoning_effort=effort,
            event_capacity=_positive_count(values["event_capacity"], "event_capacity"),
            event_max_bytes=_positive_count(values["event_max_bytes"], "event_max_bytes", 256),
            item_capacity=_positive_count(values["item_capacity"], "item_capacity"),
            item_max_bytes=_positive_count(values["item_max_bytes"], "item_max_bytes", 256),
        )
