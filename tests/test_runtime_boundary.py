import asyncio
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from codex_coordinator.coordinator import WorkerPermissions, judge_permission_overrides


async def _send(process, message):
    process.stdin.write((json.dumps(message) + "\n").encode())
    await process.stdin.drain()


async def _response(process, request_id, timeout=15):
    async def read():
        while line := await process.stdout.readline():
            message = json.loads(line)
            if message.get("id") == request_id:
                return message
        raise RuntimeError("app-server exited before responding")

    return await asyncio.wait_for(read(), timeout)


@pytest.mark.asyncio
async def test_real_app_server_initializes_with_disposable_state(tmp_path: Path):
    if shutil.which("codex") is None:
        pytest.skip("Codex CLI is not installed")
    state = tmp_path / "codex-state"
    state.mkdir()
    codex_env = os.environ.copy()
    codex_env["CODEX_HOME"] = str(state)
    process = await asyncio.create_subprocess_exec(
        "codex", "app-server", "--stdio", env=codex_env,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        await _send(process, {
            "method": "initialize", "id": 1,
            "params": {"clientInfo": {"name": "coordinator-handshake-test", "version": "1"}},
        })
        assert "result" in await _response(process, 1)
    finally:
        if process.returncode is None:
            process.terminate()
        await asyncio.wait_for(process.wait(), 3)


def test_judge_permission_profile_denies_unrelated_file_read(tmp_path: Path):
    """Prove the runtime enforces the exact profile used by one-shot judges."""
    codex = shutil.which("codex")
    if codex is None:
        pytest.skip("Codex CLI is not installed")
    evidence = tmp_path / "judge-evidence"
    evidence.mkdir()
    allowed = evidence / "allowed.txt"
    allowed.write_text("allowed")
    unrelated = tmp_path / "unrelated.txt"
    unrelated.write_text("must stay unavailable")
    assert unrelated.read_text() == "must stay unavailable"

    executable = str(Path(codex).resolve(strict=True))
    overrides = judge_permission_overrides(str(evidence), codex_command=executable)
    command = [executable, "sandbox", "--permission-profile", "coordinator_judge", "--cd", str(evidence)]
    for override in overrides:
        command.extend(("--config", override))
    command.extend((
        "--", "/bin/sh", "-c",
        'cat "$1" >/dev/null && ! cat "$2" >/dev/null 2>&1 && "$3" --version >/dev/null',
        "judge-read-probe", str(allowed), str(unrelated), executable,
    ))
    result = subprocess.run(command, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr


@pytest.mark.asyncio
async def test_real_execution_boundary_blocks_standard_temp_api_write(tmp_path: Path):
    """Exercise the actual app-server sandbox, including its subprocess boundary."""
    if shutil.which("codex") is None:
        pytest.skip("Codex CLI is not installed")
    project = tmp_path / "project"
    project.mkdir()
    outside_temp = tmp_path / "temp-api-escape"
    outside_child = tmp_path / "subprocess-escape"
    outside_link = tmp_path / "symlink-escape"
    link = project / "outside-link"
    link.symlink_to(tmp_path, target_is_directory=True)
    (project / "test_escape.py").write_text(
        "import pathlib, subprocess, sys, tempfile, unittest\n\n"
        "class BoundaryTest(unittest.TestCase):\n"
        "    def test_transitive_writes_are_blocked(self):\n"
        f"        outside_temp = pathlib.Path({str(outside_temp)!r})\n"
        f"        outside_child = pathlib.Path({str(outside_child)!r})\n"
        f"        outside_link = pathlib.Path({str(link / outside_link.name)!r})\n"
        "        with self.assertRaises(OSError):\n"
        "            with tempfile.NamedTemporaryFile(dir=outside_temp.parent, prefix=outside_temp.name):\n"
        "                pass\n"
        "        child = subprocess.run([sys.executable, '-c', "
        "'import pathlib; pathlib.Path(' + repr(str(outside_child)) + ').write_text(\"bad\")'])\n"
        "        self.assertNotEqual(child.returncode, 0)\n"
        "        with self.assertRaises(OSError):\n"
        "            outside_link.write_text('bad')\n"
    )
    targets = (outside_temp, outside_child, outside_link)
    assert not any(path.exists() for path in targets)
    # Codex persists app-server state under CODEX_HOME. Keep this test's state
    # in its disposable project rather than requiring writes to the user's home.
    state = tmp_path / "codex-state"
    state.mkdir()
    codex_env = os.environ.copy()
    codex_env["CODEX_HOME"] = str(state)
    process = await asyncio.create_subprocess_exec(
        "codex", "app-server", "--stdio",
        env=codex_env,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        await _send(process, {
            "method": "initialize",
            "id": 1,
            "params": {
                "clientInfo": {"name": "governance-boundary-test", "version": "1"},
                "capabilities": {"experimentalApi": True},
            },
        })
        assert "result" in await _response(process, 1)
        await _send(process, {"method": "initialized"})
        await _send(process, {
            "method": "command/exec",
            "id": 2,
            "params": {
                "command": [
                    "python3", "-m", "unittest", "discover", "-v",
                ],
                "cwd": str(project),
                "sandboxPolicy": WorkerPermissions(
                    "on-request", "user", "workspace-write"
                ).enforced_sandbox(project),
            },
        })
        response = await _response(process, 2)
        assert not any(path.exists() for path in targets), response
        result = response.get("result") or {}
        assert result.get("exitCode", result.get("exit_code", 1)) == 0, result
    finally:
        for target in targets:
            if target.exists():
                target.unlink()
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), 3)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
