"""Check transparent coordinator configuration without starting child sessions."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from pathlib import Path

from .compatibility import check_codex_compatibility
from .config import OperatorConfig
from .daemon import probe_local_socket


MONITOR_CONTRACT_VERSION = 2
MONITOR_RELATIVE_PATH = Path(".codex/agents/coordinator-monitor.toml")


def workspace_warnings(workspace: Path | None) -> list[str]:
    if workspace is None:
        return []
    monitor = workspace / MONITOR_RELATIVE_PATH
    if not monitor.exists():
        return [f"project-scoped monitor definition is missing: {monitor}"]
    try:
        text = monitor.read_text()
    except OSError as exc:
        return [f"cannot inspect project-scoped monitor definition {monitor}: {exc}"]
    match = re.search(r"^# coordinator-contract-version: (\d+)$", text, re.MULTILINE)
    warnings = []
    version = int(match.group(1)) if match else None
    if version != MONITOR_CONTRACT_VERSION:
        warnings.append(
            "project-scoped monitor contract is stale: expected version "
            f"{MONITOR_CONTRACT_VERSION}, found "
            f"{version if version is not None else 'unversioned'}"
        )
    if "every control-plane command" not in text or "retry once" not in text:
        warnings.append(
            "project-scoped monitor lacks the required local-transport escalation contract"
        )
    return warnings


def check(
    config: OperatorConfig, projects: list[str], *, require_socket: bool = False,
    workspace: Path | None = None,
) -> dict:
    if not config.projects:
        raise ValueError("configure at least one named project")
    check_codex_compatibility(config.codex_command)
    executable = shutil.which(config.codex_command)
    if executable is None:
        raise ValueError(f"Codex executable {config.codex_command!r} disappeared after compatibility check")
    try:
        login = subprocess.run([executable, "login", "status"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError(f"cannot check Codex sign-in with {executable}") from exc
    if login.returncode:
        raise ValueError("Codex is not signed in; run `codex login` before coordination")
    selected = projects or list(config.projects)
    unknown = sorted(set(selected) - set(config.projects))
    if unknown:
        raise ValueError(f"unknown configured projects: {unknown}")
    try:
        config.socket_path.lstat()
        ready = True
    except FileNotFoundError:
        ready = False
    except OSError as exc:
        raise ValueError(
            f"cannot access host Codex app-server socket {config.socket_path}: {exc}"
        ) from exc
    if ready:
        try:
            probe_local_socket(config.socket_path)
        except PermissionError as exc:
            raise ValueError(
                f"cannot access host Codex app-server socket {config.socket_path}: {exc}"
            ) from exc
        except ConnectionError as exc:
            cause = exc.__cause__
            if isinstance(cause, PermissionError):
                raise ValueError(
                    f"cannot access host Codex app-server socket {config.socket_path}: {cause}"
                ) from exc
            raise ValueError(str(exc)) from exc
    elif require_socket:
        raise ValueError(
            f"host Codex app-server socket is unavailable: {config.socket_path}; "
            "start the app-server before coordination"
        )
    return {
        "ok": True, "socketReady": ready,
        "socketPath": str(config.socket_path),
        "projects": [{"name": name, "path": str(config.projects[name])} for name in selected],
        "workspaceWarnings": workspace_warnings(workspace),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="operator TOML configuration")
    parser.add_argument("--project", action="append", default=[], help="configured project name; repeat")
    parser.add_argument("--require-socket", action="store_true", help="fail unless the host socket is reachable")
    args = parser.parse_args()
    try:
        result = check(
            OperatorConfig.load(path=args.config), args.project,
            require_socket=args.require_socket,
            workspace=args.config.resolve().parent if args.config else None,
        )
    except (ValueError, ConnectionError) as exc:
        parser.exit(2, f"preflight failed: {exc}\n")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
