import subprocess
from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_live_e2e_make_target_defaults_to_parent_monitor_mode():
    result = subprocess.run(
        ["make", "-n", "live-e2e"],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert "CODEX_COORDINATOR_PROJECTS=" in result.stdout
    assert "--parent \"coordination\"" in result.stdout
    assert "--first \"inventory-app\"" in result.stdout
    assert "--second \"inventory-report\"" in result.stdout
    assert str(ROOT / "examples" / "coordination-workspace") in result.stdout
    assert "already the service-managed child" in result.stdout
    assert "do not invoke another coordinator" in result.stdout


def test_live_e2e_make_target_accepts_custom_operator_config():
    result = subprocess.run(
        ["make", "-n", "live-e2e", "LIVE_E2E_CONFIG=/tmp/operator.toml"],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert '--config "/tmp/operator.toml"' in result.stdout
    assert "CODEX_COORDINATOR_PROJECTS=" not in result.stdout
