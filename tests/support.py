"""Shared fixtures for the boundary these tests exercise.

The coordinator names a permission profile per project and refuses a session
unless the runtime reports back that it selected that same profile (#0021), so
a fake client has to answer ``thread/start`` the way the runtime does and a
policy has to be given a resolved boundary rather than a mode string.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from codex_coordinator.profiles import PermissionProfile, builtin_profile


#: The profile shape an operator writes for a worker: writable project, no
#: network, and the two temporary roots demoted so the boundary matches the
#: sandbox literal this project used to send.
WORKER_PROFILE_ID = "worker_workspace"
WORKER_HOME_CONFIG = (
    'default_permissions = ":read-only"\n'
    "\n"
    f"[permissions.{WORKER_PROFILE_ID}]\n"
    'extends = ":workspace"\n'
    'filesystem = { ":tmpdir" = "read", ":slash_tmp" = "read" }\n'
)


def worker_profile(profile_id: str = WORKER_PROFILE_ID) -> PermissionProfile:
    return PermissionProfile(
        id=profile_id, extends=":workspace", chain=(profile_id, ":workspace"),
        builtin=":workspace", writable=True,
        filesystem={":tmpdir": "read", ":slash_tmp": "read"}, network={},
        source="test fixture",
    )


def read_only_profile() -> PermissionProfile:
    return builtin_profile(":read-only")


def codex_home(root: Path, config: str = WORKER_HOME_CONFIG) -> Path:
    """An operator Codex home holding the profiles a test selects by id."""
    home = root / "codex-home"
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.toml").write_text(config)
    return home


def started_thread(
    params: dict[str, Any], thread_id: str, *, extends: str | None = ":workspace",
) -> dict[str, Any]:
    """A ``thread/start`` reply carrying the provenance the runtime reports.

    The id is echoed from the request because that is what the real server does
    when the home defines the profile; a test that wants the mismatch failure
    passes its own reply instead.
    """
    return {
        "thread": {"id": thread_id},
        "activePermissionProfile": {"id": params["permissions"], "extends": extends},
    }
