from __future__ import annotations

import asyncio
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


def default_codex_home() -> Path:
    return Path.home() / ".codex"


def default_daemon_socket(codex_home: Path | None = None) -> Path:
    home = default_codex_home() if codex_home is None else Path(codex_home)
    return home / DAEMON_SOCKET


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


async def ensure_daemon(
    *,
    socket_path: Path,
    codex_command: str = "codex",
    codex_home: Path | None = None,
    command_timeout: float = 30,
    connect_timeout: float = 10,
) -> None:
    if socket_path != default_daemon_socket(codex_home):
        if not socket_path.exists() and not socket_path.is_symlink():
            raise RuntimeError(
                f"custom Codex socket is unavailable: {socket_path}; start an app-server listener there first"
            )
        validate_local_socket(socket_path)
        return
    if socket_path.exists() or socket_path.is_symlink():
        validate_local_socket(socket_path)
    # The daemon reads its whole configuration — permission profiles included —
    # from this home, so it is the same home the coordinator resolved profiles
    # against and never whichever one the developer's other sessions use.
    environment = None if codex_home is None else {
        **os.environ, "CODEX_HOME": str(codex_home),
    }
    process = await asyncio.create_subprocess_exec(
        codex_command,
        "app-server",
        "daemon",
        "start",
        env=environment,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), command_timeout)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        raise RuntimeError("timed out starting Codex app-server daemon")
    if process.returncode:
        detail = stderr.decode(errors="replace") or stdout.decode(errors="replace")
        raise RuntimeError(f"codex app-server daemon start failed: {detail}")

    deadline = asyncio.get_running_loop().time() + connect_timeout
    while not socket_path.exists():
        if asyncio.get_running_loop().time() >= deadline:
            raise RuntimeError(f"Codex socket did not appear: {socket_path}")
        await asyncio.sleep(0.2)
    validate_local_socket(socket_path)
