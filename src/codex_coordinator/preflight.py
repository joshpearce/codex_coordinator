"""Check a local operator configuration without starting worker sessions."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path

from .compatibility import check_codex_compatibility
from .config import OperatorConfig
from .coordinator import refuse_worker_project_rules
from .daemon import default_daemon_socket, probe_local_socket


def check(config: OperatorConfig, projects: list[Path], *, require_socket: bool = False) -> dict:
    if not config.allowed_roots:
        raise ValueError("configure at least one allowed root")
    version = check_codex_compatibility(config.codex_command)
    executable = shutil.which(config.codex_command)
    if executable is None:
        raise ValueError(
            f"Codex executable {config.codex_command!r} disappeared after compatibility check; "
            "install Codex CLI or set codex_command"
        )
    try:
        login = subprocess.run(
            [executable, "login", "status"], capture_output=True, text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError(
            f"cannot check Codex sign-in with {executable}; "
            "verify the executable and run `codex login status`"
        ) from exc
    if login.returncode:
        raise ValueError("Codex is not signed in; run `codex login` before coordination")
    validated: list[dict[str, str]] = []
    for raw in projects:
        project = raw.expanduser().resolve(strict=True)
        if not project.is_dir() or not any(
            project == root or root in project.parents for root in config.allowed_roots
        ):
            raise ValueError(f"project is outside configured allowed roots: {raw}")
        # A project whose tree carries Codex rules of its own gets no session,
        # so report that here rather than at the first start (#0017).
        refuse_worker_project_rules(project)
        worker = config.permissions_for(project)
        validated.append({
            "project": str(project),
            "sandboxMode": worker.sandbox_mode,
            "approvalPolicy": worker.approval_policy,
            "permissionsSource": worker.source,
            "permissionsDigest": worker.digest,
            # Rules the coordinator decides without a judge, or null when every
            # command of this project is judged.
            "execPolicy": None if worker.exec_policy is None else worker.exec_policy.provenance(),
        })
    ready = config.socket_path.exists() or config.socket_path.is_symlink()
    if ready:
        probe_local_socket(config.socket_path)
    elif config.socket_path != default_daemon_socket():
        raise ValueError(
            f"custom Codex socket is unavailable: {config.socket_path}; start an app-server listener there first"
        )
    elif require_socket:
        raise ValueError(
            f"Codex socket is unavailable: {config.socket_path}; start the daemon or omit --require-socket"
        )
    return {
        "ok": True,
        "codexVersion": version,
        "socketReady": ready,
        "socketPath": str(config.socket_path),
        "projects": validated,
        "judgePolicyConfigured": bool(config.judge_policy.strip()),
        "approvalMode": config.approval_mode,
        "constitutionConfigured": bool(config.constitution_text),
        "projectConstitutions": sorted(
            str(project) for project in config.project_constitution_paths
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="operator-owned TOML configuration")
    parser.add_argument("--project", type=Path, action="append", default=[], help="worker project to validate; repeat")
    parser.add_argument("--require-socket", action="store_true", help="fail unless a private, reachable Codex socket already exists")
    args = parser.parse_args()
    config = OperatorConfig.load(path=args.config)
    print(json.dumps(check(config, args.project, require_socket=args.require_socket), sort_keys=True))


if __name__ == "__main__":
    main()
