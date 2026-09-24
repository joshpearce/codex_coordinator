import os
import shutil
import subprocess
from pathlib import Path


TEMPLATE = Path(__file__).parents[1] / "examples" / "coordination-workspace" / "Makefile"


def executable(path: Path, text: str) -> None:
    path.write_text("#!/bin/bash\nset -eu\n" + text)
    path.chmod(0o755)


def workspace(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    root = tmp_path / "workspace"
    root.mkdir()
    shutil.copy(TEMPLATE, root / "Makefile")
    (root / "operator.toml").write_text("[projects]\n")
    tools = tmp_path / "bin"
    tools.mkdir()
    job = tmp_path / "job"
    executable(tools / "preflight", "exit 0\n")
    executable(tools / "service", "exit 99\n")
    executable(
        tools / "launchctl",
        'case "$1" in\n'
        '  submit) touch "$FAKE_JOB" ;;\n'
        '  list) test -f "$FAKE_JOB" ;;\n'
        '  remove) rm -f "$FAKE_JOB" ;;\n'
        'esac\n',
    )
    executable(
        tools / "curl",
        'if [[ "$*" == *"/shutdown"* ]]; then rm -f "$FAKE_JOB"; exit 0; fi\n'
        'test -f "$FAKE_JOB"\n',
    )
    env = dict(os.environ, PATH=f"{tools}:{os.environ['PATH']}", FAKE_JOB=str(job))
    return root, env


def run_make(root: Path, env: dict[str, str], target: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["make", target, "PREFLIGHT=preflight", "SERVICE=service"],
        cwd=root, env=env, text=True, capture_output=True,
    )


def test_launcher_uses_external_supervisor_and_health_for_start_status_stop(tmp_path):
    root, env = workspace(tmp_path)
    makefile = (root / "Makefile").read_text()
    assert "nohup" not in makefile
    assert "launchctl submit" in makefile
    assert "/health" in makefile

    started = run_make(root, env, "start")
    assert started.returncode == 0, started.stderr
    assert "supervised" in started.stdout and "healthy" in started.stdout
    assert not (root / ".coordinator.pid").exists()

    status = run_make(root, env, "status")
    assert status.returncode == 0
    assert "healthy" in status.stdout

    stopped = run_make(root, env, "stop")
    assert stopped.returncode == 0
    assert not (root / ".coordinator.port").exists()


def test_status_explains_stale_port_state(tmp_path):
    root, env = workspace(tmp_path)
    (root / ".coordinator.port").write_text("8765\n")

    status = run_make(root, env, "status")
    assert status.returncode == 2
    assert "stale PID/port state" in status.stdout
    assert "make stop" in status.stdout
