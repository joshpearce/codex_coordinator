import importlib.util
from pathlib import Path

import pytest


gate_path = Path(__file__).resolve().parents[1] / "scripts/judge_live_gate.py"
spec = importlib.util.spec_from_file_location("judge_live_gate", gate_path)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


@pytest.mark.asyncio
async def test_live_judge_gate_requires_structured_denial(monkeypatch, capsys):
    async def runner(prompt):
        assert "deny every request" in prompt
        return '{"verdict":"deny","reason":"smoke policy"}'

    monkeypatch.setattr(gate, "codex_exec_json_runner", runner)
    await gate.main()
    assert '"liveJudgeGate": "passed"' in capsys.readouterr().out


@pytest.mark.asyncio
async def test_live_judge_gate_surfaces_runner_failure(monkeypatch):
    async def runner(_prompt):
        raise RuntimeError("profile probe failed")

    monkeypatch.setattr(gate, "codex_exec_json_runner", runner)
    with pytest.raises(RuntimeError, match="profile probe failed"):
        await gate.main()
