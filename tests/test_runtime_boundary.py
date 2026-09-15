import asyncio
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from codex_coordinator.coordinator import judge_permission_overrides


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
                "sandboxPolicy": legacy_sandbox_literal(project),
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


#: A profile that reproduces ``WorkerPermissions.enforced_sandbox`` exactly.
#:
#: Probed against the pinned CLI. ``extends = ":workspace"`` alone matches the
#: sandbox literal on every axis except ``/tmp`` and ``$TMPDIR``, which the
#: literal excludes and which ``:workspace`` leaves writable. The ``filesystem``
#: map's ``:slash_tmp`` and ``:tmpdir`` keys are the profile spellings of
#: ``excludeSlashTmp`` and ``excludeTmpdirEnvVar``: setting each to ``"read"``
#: removes writability and leaves the reads the literal also permits. The
#: workspace root grant still wins for the project itself when the project
#: lives inside ``$TMPDIR``, which is what ``tmp_path`` gives us.
#:
#: Three traps found while probing this shape. An unrecognized key under
#: ``[permissions.<id>]`` is accepted and silently ignored rather than refused,
#: and so is an unrecognized ``filesystem`` token — ``:tmp``, ``:temp`` and
#: ``:system_tmp`` all parse and leave both temporary roots writable, while an
#: unrecognized *value* fails the load. Naming a real path rather than the token does not work
#: either: ``"/tmp" = "read"`` and ``"/private/tmp" = "read"`` both leave
#: ``/tmp`` writable, so only the token demotes. ``"deny"`` and ``"none"`` do
#: take effect on any path, but they remove the reads the literal allows, which
#: is a different boundary rather than a stricter spelling of this one.
def legacy_sandbox_literal(project: Path) -> dict:
    """The boundary this project sent before it migrated to profiles (#0021).

    `WorkerPermissions.enforced_sandbox` built exactly this and is gone. It is
    kept here because it is the thing the profile has to be equivalent to: a
    comparison against the current code would only prove the current code
    agrees with itself.
    """
    return {
        "type": "workspaceWrite",
        "writableRoots": [str(project.resolve())],
        "networkAccess": False,
        "excludeTmpdirEnvVar": True,
        "excludeSlashTmp": True,
    }


EQUIVALENT_WORKSPACE_PROFILE = "worker_workspace"
EQUIVALENT_WORKSPACE_CONFIG = (
    'default_permissions = ":read-only"\n'
    "\n"
    f"[permissions.{EQUIVALENT_WORKSPACE_PROFILE}]\n"
    'extends = ":workspace"\n'
    'filesystem = { ":tmpdir" = "read", ":slash_tmp" = "read" }\n'
)

#: The same escapes ``test_real_execution_boundary_blocks_standard_temp_api_write``
#: exercises, reported per axis so two boundaries can be compared rather than
#: each merely passing. Reads stay allowed under both, so a blanket denial
#: cannot masquerade as confinement.
BOUNDARY_PROBE = r'''
import json, os, pathlib, subprocess, sys, tempfile

project = pathlib.Path(sys.argv[1])
outside = pathlib.Path(sys.argv[2])
link = project / "outside-link"
observed = {}


def check(name, action):
    try:
        action()
    except Exception:
        observed[name] = "blocked"
    else:
        observed[name] = "allowed"


def temp_api():
    with tempfile.NamedTemporaryFile(dir=str(outside), prefix="temp-api"):
        pass


def child():
    finished = subprocess.run([
        sys.executable, "-c",
        "import pathlib, sys; pathlib.Path(sys.argv[1]).write_text('bad')",
        str(outside / "subprocess-escape.txt"),
    ], capture_output=True)
    if finished.returncode != 0:
        raise OSError("child write refused")


check("in_project_write", lambda: (project / "allowed.txt").write_text("x"))
check("outside_read", lambda: (outside / "seed.txt").read_text())
check("outside_write", lambda: (outside / "escape.txt").write_text("x"))
check("outside_tempfile_api", temp_api)
check("outside_subprocess_write", child)
check("symlink_escape_write", lambda: (link / "symlink-escape.txt").write_text("x"))
check("slash_tmp_read", lambda: next(pathlib.Path("/tmp").iterdir()))
check("slash_tmp_write", lambda: pathlib.Path("/tmp/boundary-probe.txt").write_text("x"))
tmpdir = os.environ.get("TMPDIR")
check(
    "tmpdir_env_write",
    (lambda: (pathlib.Path(tmpdir) / "boundary-probe.txt").write_text("x"))
    if tmpdir else (lambda: (_ for _ in ()).throw(OSError("no TMPDIR"))),
)
print("BOUNDARY_PROBE" + json.dumps(observed))
'''

EXPECTED_CONFINEMENT = {
    "in_project_write": "allowed",
    "outside_read": "allowed",
    "outside_write": "blocked",
    "outside_tempfile_api": "blocked",
    "outside_subprocess_write": "blocked",
    "symlink_escape_write": "blocked",
    "slash_tmp_read": "allowed",
    "slash_tmp_write": "blocked",
    "tmpdir_env_write": "blocked",
}


async def _start_app_server(codex_home: Path):
    """Start a real app-server against a disposable Codex home."""
    codex_env = os.environ.copy()
    codex_env["CODEX_HOME"] = str(codex_home)
    process = await asyncio.create_subprocess_exec(
        "codex", "app-server", "--stdio", env=codex_env,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await _send(process, {
        "method": "initialize", "id": 1,
        "params": {
            "clientInfo": {"name": "boundary-equivalence-test", "version": "1"},
            "capabilities": {"experimentalApi": True},
        },
    })
    assert "result" in await _response(process, 1)
    await _send(process, {"method": "initialized"})
    return process


async def _stop_app_server(process) -> None:
    if process.returncode is None:
        process.terminate()
    try:
        await asyncio.wait_for(process.wait(), 3)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()


async def _probe_boundary(process, request_id: int, case: Path, boundary: dict) -> dict:
    """Run the escape probe under one boundary and report it axis by axis."""
    project = case / "project"
    outside = case / "outside"
    project.mkdir(parents=True)
    outside.mkdir(parents=True)
    (outside / "seed.txt").write_text("readable from either boundary")
    (project / "outside-link").symlink_to(outside, target_is_directory=True)
    await _send(process, {
        "method": "command/exec", "id": request_id,
        "params": {
            "command": ["python3", "-c", BOUNDARY_PROBE, str(project), str(outside)],
            "cwd": str(project),
            **boundary,
        },
    })
    response = await _response(process, request_id, timeout=60)
    assert "error" not in response, response
    stdout = (response.get("result") or {}).get("stdout", "")
    assert "BOUNDARY_PROBE" in stdout, response
    return json.loads(stdout.split("BOUNDARY_PROBE", 1)[1].splitlines()[0])


@pytest.mark.asyncio
async def test_permission_profile_confines_writes_like_the_sandbox_literal(tmp_path: Path):
    """Issue #0021 stage 1: the migration's confinement precondition.

    The coordinator sends a sandbox literal today. Before that shape can be
    replaced by a named profile, the profile has to confine writes the same
    way. This runs one escape probe under both boundaries on the same
    app-server and compares them axis by axis, so an equivalence that stops
    holding fails here rather than in a live run.
    """
    if shutil.which("codex") is None:
        pytest.skip("Codex CLI is not installed")
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    (codex_home / "config.toml").write_text(EQUIVALENT_WORKSPACE_CONFIG)
    process = await _start_app_server(codex_home)
    try:
        literal = await _probe_boundary(
            process, 10, tmp_path / "literal",
            {"sandboxPolicy": legacy_sandbox_literal(tmp_path / "literal" / "project")},
        )
        profile = await _probe_boundary(
            process, 11, tmp_path / "profile",
            {"permissionProfile": EQUIVALENT_WORKSPACE_PROFILE},
        )
    finally:
        await _stop_app_server(process)
    # Assert the outcome as well as the match: two boundaries that were both
    # wide open would agree with each other and prove nothing.
    assert literal == EXPECTED_CONFINEMENT
    assert profile == EXPECTED_CONFINEMENT


@pytest.mark.asyncio
async def test_thread_start_reports_profile_provenance_the_sandbox_literal_lacks(tmp_path: Path):
    """Issue #0021: only a profile gives a thread's boundary a provenance.

    A thread's sandbox cannot be exercised without a model turn — the one
    unsandboxed escape hatch, ``thread/shellCommand``, documents itself as
    running with full access rather than inheriting the thread policy — so what
    is asserted here is the shape the coordinator would read back, and the
    lossiness of the legacy view it reads back today.
    """
    if shutil.which("codex") is None:
        pytest.skip("Codex CLI is not installed")
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    (codex_home / "config.toml").write_text(EQUIVALENT_WORKSPACE_CONFIG)
    project = tmp_path / "project"
    project.mkdir()
    process = await _start_app_server(codex_home)
    try:
        started = {}
        for request_id, label, boundary in (
            (20, "profile", {"permissions": EQUIVALENT_WORKSPACE_PROFILE}),
            (21, "literal", {"sandbox": "workspace-write"}),
        ):
            await _send(process, {
                "method": "thread/start", "id": request_id,
                "params": {
                    "cwd": str(project), "ephemeral": True,
                    "runtimeWorkspaceRoots": [str(project)], **boundary,
                },
            })
            response = await _response(process, request_id, timeout=30)
            assert "error" not in response, response
            started[label] = response["result"]
        # The schema states the mutual exclusion only in prose, so the
        # compatibility gate cannot assert it. The runtime enforces it, and
        # this is where that is checked.
        await _send(process, {
            "method": "thread/start", "id": 22,
            "params": {
                "cwd": str(project), "ephemeral": True,
                "permissions": EQUIVALENT_WORKSPACE_PROFILE,
                "sandbox": "workspace-write",
            },
        })
        both = await _response(process, 22, timeout=30)
    finally:
        await _stop_app_server(process)

    assert both.get("error", {}).get("code") == -32600, both

    assert started["profile"]["activePermissionProfile"] == {
        "id": EQUIVALENT_WORKSPACE_PROFILE, "extends": ":workspace",
    }
    # Sending the legacy literal detaches the thread from the profile system
    # rather than selecting a profile, so the boundary has no provenance.
    assert started["literal"]["activePermissionProfile"] is None
    for label, result in started.items():
        assert result["runtimeWorkspaceRoots"] == [str(project)], label
        # The legacy readback carries no roots under either shape: the writable
        # scope lives in runtimeWorkspaceRoots. An audit that reads `sandbox`
        # back cannot see what the thread may write.
        assert result["sandbox"]["writableRoots"] == [], label


@pytest.mark.asyncio
async def test_a_pinned_default_keeps_the_boundary_stable_across_trust(tmp_path: Path):
    """Issue #0021: the implicit default follows project trust records.

    With no `default_permissions`, the same unconfigured thread resolves to
    `:read-only` before a trust record exists for its cwd and to `:workspace`
    after one does — the runtime writes that record into the home the first time
    a thread runs there. An explicitly selected profile must not drift the same
    way, because a boundary that depends on how many times a project has been
    used before is not a boundary.
    """
    if shutil.which("codex") is None:
        pytest.skip("Codex CLI is not installed")
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    (codex_home / "config.toml").write_text(EQUIVALENT_WORKSPACE_CONFIG)
    project = tmp_path / "project"
    project.mkdir()

    async def start() -> dict:
        process = await _start_app_server(codex_home)
        try:
            await _send(process, {
                "method": "thread/start", "id": 30,
                "params": {
                    "cwd": str(project), "ephemeral": True,
                    "runtimeWorkspaceRoots": [str(project)],
                    "permissions": EQUIVALENT_WORKSPACE_PROFILE,
                },
            })
            response = await _response(process, 30, timeout=30)
            assert "error" not in response, response
            return response["result"]
        finally:
            await _stop_app_server(process)

    first = await start()
    trusted = (codex_home / "config.toml").read_text()
    second = await start()

    assert first["activePermissionProfile"] == second["activePermissionProfile"]
    assert first["activePermissionProfile"]["id"] == EQUIVALENT_WORKSPACE_PROFILE
    # The runtime did write into the home, so the two runs really are the
    # before-and-after this is asserting about.
    assert "trust_level" in trusted, trusted
