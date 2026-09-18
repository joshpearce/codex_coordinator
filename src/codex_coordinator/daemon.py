from __future__ import annotations

import os
import socket
import stat
from pathlib import Path


#: Where a Codex app-server daemon puts its control socket, relative to the home
#: it was started with, so the default follows the operator's configured Codex
#: home rather than ``Path.home()`` (#0021).
#:
#: The daemon is not a way to isolate an arbitrary home: ``codex app-server
#: daemon start`` refuses unless the managed standalone install exists under the
#: home it is given. A deployment whose home is not the managed one runs its own
#: listener and names it in ``socket_path``, which is what the live harness does.
DAEMON_SOCKET = "app-server-control/app-server-control.sock"


def default_daemon_socket() -> Path:
    """Return the standard socket in the host user's active Codex home."""
    return Path.home() / ".codex" / DAEMON_SOCKET


def validate_local_socket(socket_path: Path) -> None:
    """Reject unsafe local connection targets before opening the transport."""
    path = Path(socket_path)
    parent = path.parent
    parent_info = parent.stat()
    if not stat.S_ISDIR(parent_info.st_mode):
        raise ValueError(f"Codex socket parent is not a directory: {parent}")
    if parent_info.st_uid != os.getuid() or parent_info.st_mode & 0o022:
        raise ValueError(f"Codex socket parent must be owner-controlled: {parent}")
    info = path.lstat()
    if not stat.S_ISSOCK(info.st_mode):
        raise ValueError(f"Codex connection path is not a Unix socket: {path}")
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError(f"Codex socket must be owned by this user and private: {path}")


def probe_local_socket(socket_path: Path, *, timeout: float = 2) -> None:
    """Confirm a validated socket has a reachable listener."""
    validate_local_socket(socket_path)
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(timeout)
            connection.connect(str(socket_path))
    except OSError as exc:
        raise ConnectionError(
            f"Codex socket {socket_path} is not reachable; "
            "start or restart the app-server daemon"
        ) from exc
