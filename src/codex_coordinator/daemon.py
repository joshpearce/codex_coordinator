from __future__ import annotations

import asyncio
from pathlib import Path


async def ensure_daemon(
    *,
    socket_path: Path,
    codex_command: str = "codex",
    command_timeout: float = 30,
    connect_timeout: float = 10,
) -> None:
    process = await asyncio.create_subprocess_exec(
        codex_command,
        "app-server",
        "daemon",
        "start",
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
