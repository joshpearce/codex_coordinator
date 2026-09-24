import os
import shutil
import subprocess
import tomllib
from pathlib import Path


ROOT = Path(__file__).parents[1]
TEMPLATE = ROOT / "examples" / "coordination-workspace" / "Makefile"
GUIDANCE = ROOT / "examples" / "coordination-workspace" / "AGENTS.md"
BOOTSTRAP = ROOT / "docs" / "coordination-workspace.md"
MONITOR = ROOT / "examples" / "coordination-workspace" / ".codex" / "agents" / "coordinator-monitor.toml"


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


def test_parent_guidance_delegates_waiting_to_bounded_monitor():
    guidance = GUIDANCE.read_text()

    assert "dedicated\n`coordinator_monitor` custom subagent" in guidance
    assert "`.codex/agents/coordinator-monitor.toml`" in guidance
    assert "small, bounded brief" in guidance
    assert "service URL" in guidance
    assert "session ID-to-project/task map" in guidance
    assert "GET /events?after=N&wait=30" in guidance
    assert "On `timeout`, it continues waiting without reporting to the parent" in guidance
    assert "must not\nfilter raw app-server schemas, busy-loop, or use shell sleeps" in guidance


def test_monitor_handoff_and_parent_responsibilities_are_explicit():
    guidance = GUIDANCE.read_text()
    compact = " ".join(guidance.split())

    for condition in (
        "On `shutdown`",
        "expired cursor",
        "changed service ID",
        "connection loss",
        "uncertain terminal state",
        "On a 410",
        "`recovery.resumeAfter`",
    ):
        assert condition in guidance
    assert "actionable child\nprogress" in guidance
    assert "a terminal state, or a monitoring\nfailure" in guidance
    assert "last safe cursor" in guidance
    assert "not decide task scope" in guidance
    assert "neither a second coordinator nor an authorization boundary" in compact
    assert "no empty, timeout, or non-actionable progress messages" in guidance
    assert "main parent retains task\ndecisions" in guidance
    assert "focused child follow-ups, cancellation, user questions, and final\nverification" in guidance
    assert "use one long `wait_agent` call" in guidance
    assert "re-arm it only if that collaboration wait itself expires" in guidance


def test_starter_goal_keeps_routine_waits_out_of_parent_context():
    bootstrap = BOOTSTRAP.read_text()
    goal = "/goal" + bootstrap.split("```text\n/goal", 1)[1].split("\n```", 1)[0]

    assert "dedicated monitoring subagent" in goal
    assert "only the service URL, session map, cursor, recovery rules, and reporting" in goal
    assert "reports only actionable progress, terminal state, or a\nmonitoring failure" in goal
    assert "never empty or timeout updates" in goal
    assert "one long collaboration wait instead of repeated short\nwaits" in goal
    assert "retain responsibility for task decisions, focused follow-ups,\ncancellation, user questions, and final verification" in goal
    assert "poll conservatively" not in goal
    assert "routine waiting out of the main coordination context" in bootstrap


def test_project_scoped_monitor_agent_matches_documented_contract():
    monitor = tomllib.loads(MONITOR.read_text())
    instructions = monitor["developer_instructions"]

    assert set(monitor) == {
        "name", "description", "model", "model_reasoning_effort",
        "sandbox_mode", "developer_instructions",
    }
    assert monitor["name"] == "coordinator_monitor"
    assert monitor["model"] == "gpt-6-luna"
    assert monitor["model_reasoning_effort"] == "low"
    assert monitor["sandbox_mode"] == "workspace-write"
    for requirement in (
        "GET /events?after=N&wait=30",
        "On timeout, continue waiting without reporting",
        "HTTP 410",
        "recovery.resumeAfter",
        "Do not send empty",
        "Do not call send_message",
        "every control-plane command",
        "retry once",
        "not a second coordinator",
    ):
        assert requirement in instructions
    bootstrap = BOOTSTRAP.read_text()
    assert "coordinator-monitor.toml" in bootstrap
    assert "`coordinator_monitor` agent" in bootstrap
