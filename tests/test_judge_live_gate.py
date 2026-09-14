import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest


gate_path = Path(__file__).resolve().parents[1] / "scripts/judge_live_gate.py"
spec = importlib.util.spec_from_file_location("judge_live_gate", gate_path)
gate = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = gate
spec.loader.exec_module(gate)


def _constitution_texts() -> dict[str, str]:
    constitution = gate.load_constitution()
    texts = {constitution.overall.source: constitution.overall.text}
    texts.update(
        {document.source: document.text for document in constitution.projects.values()}
    )
    return texts


def test_scenarios_are_not_named_by_any_constitution():
    """The constitutions must decide these requests by principle, not by name.

    A constitution that spelled out `pip install rich` would make the live gate
    a string-matching exercise and would reproduce the authoring failure the
    per-project tier exists to remove.
    """
    texts = {source: text.lower() for source, text in _constitution_texts().items()}

    def names(token: str, text: str) -> bool:
        # Whole-token match: "pip" must not fire on "piping", and a token may
        # begin with punctuation such as "--user".
        return re.search(rf"(?<![\w-]){re.escape(token.lower())}(?![\w-])", text) is not None

    named = [
        (scenario.name, token, Path(source).name)
        for scenario in gate.SCENARIOS
        for token in scenario.distinctive
        for source, text in texts.items()
        if names(token, text)
    ]
    assert named == []

    # The guard is only meaningful if every scenario carries tokens to check.
    assert all(scenario.distinctive for scenario in gate.SCENARIOS)


def test_scenario_catalogue_covers_every_governed_project_in_both_directions():
    by_project: dict[str, set[str]] = {}
    for scenario in gate.SCENARIOS:
        assert scenario.expected in {"approve_once", "deny"}
        assert scenario.principle.strip()
        by_project.setdefault(scenario.project, set()).add(scenario.expected)

    # Drift guard: the governed set comes from the example operator.toml, so
    # adding or removing a project there fails here until scenarios follow.
    assert set(by_project) == set(gate.governed_projects())
    # An all-deny gate cannot detect a judge that denies everything, and an
    # all-approve gate cannot detect one that approves everything.
    for project, verdicts in by_project.items():
        assert verdicts == {"approve_once", "deny"}, project

    names = [scenario.name for scenario in gate.SCENARIOS]
    assert len(names) == len(set(names))


def test_gate_policy_is_the_one_the_operator_configuration_wires_up():
    """The gate must inherit `[project_constitutions]`, not restate it."""
    config = gate.load_operator_config()
    constitution = gate.load_constitution()

    assert config.approval_mode == "service"
    assert constitution is config.constitution
    assert set(constitution.projects) == set(config.project_constitution_paths)
    for project, policy_path in config.project_constitution_paths.items():
        assert constitution.projects[project].source == str(policy_path)

    # The rendered copy must carry the checked-in text verbatim, or the gate
    # would be judging against policy nobody reviewed. Resolve each expected
    # file from the PROJECT, never from the document's own source: deriving it
    # from the source would pass even if two projects were wired to one
    # document, or to each other's.
    examples = Path(__file__).resolve().parents[1] / "examples"
    assert constitution.overall.text == (examples / "operator/constitution.md").read_text()
    for project, document in constitution.projects.items():
        assert project == (examples / project.name).resolve()
        expected_file = examples / "operator" / f"{project.name}.constitution.md"
        assert document.source == str(
            Path(constitution.overall.source).parent / expected_file.name
        )
        assert document.text == expected_file.read_text()

    # Distinct projects must not share a document; identical digests would mean
    # one worker is judged under another's policy.
    digests = [document.digest for document in constitution.projects.values()]
    assert len(digests) == len(set(digests))


def test_each_scenario_normalizes_and_reaches_the_judge_with_its_own_policy():
    constitution = gate.load_constitution()
    for index, scenario in enumerate(gate.SCENARIOS):
        _policy, case = gate.build_case(scenario, index)
        assert case.declared_intent["command"] == scenario.command
        assert case.declared_intent["reason"] == scenario.reason
        assert Path(case.project).name == scenario.project

        # The descriptor reaches the judge, and the request cannot carry one:
        # it comes from the ledger the coordination path writes.
        assignment = gate.assignment_for(scenario)
        assert case.assignment["text"] == assignment
        # A scenario carrying its own assignment is a follow-up turn, recorded
        # after the goal prompt that opened the session.
        assert case.assignment["digest"]
        assert case.assignment["turn"] == (1 if scenario.assignment is None else 2)
        assert "assignment" not in case.request

        policy = constitution.trusted_policy(case.project)
        expected = constitution.projects[gate.governed_projects()[scenario.project]]
        assert policy["project_constitution"]["text"] == expected.text
        assert policy["project_constitution"]["source"] == expected.source
        # The other project's document must never reach this judge.
        others = [
            document.text
            for project, document in constitution.projects.items()
            if project.name != scenario.project
        ]
        rendered = json.dumps(policy)
        assert all(text not in rendered for text in others)


def test_catalogue_pairs_one_command_against_two_assignments():
    """The gate must contain a case that only the task descriptor decides.

    Without it, the live gate could pass while the judge ignored the
    assignment entirely and read necessity out of the worker's own reason.
    """
    by_command: dict[str, list] = {}
    for scenario in gate.SCENARIOS:
        by_command.setdefault(scenario.command, []).append(scenario)
    pairs = [
        scenarios for scenarios in by_command.values()
        if len({scenario.expected for scenario in scenarios}) > 1
    ]
    assert pairs, "no scenario pair differs only in its assignment"
    for scenarios in pairs:
        assert len({scenario.project for scenario in scenarios}) == 1
        assert len({scenario.reason for scenario in scenarios}) == 1
        # Every other input is identical, so the constitutions, the ceiling and
        # the worker's evidence cannot be what separates the verdicts.
        assert len({gate.assignment_for(scenario) for scenario in scenarios}) == len(scenarios)

    # What separates them is whether the task names the work the command does.
    # The denied side's assignment must not simply forbid the command: the judge
    # has to find the action unnecessary, not prohibited.
    for scenarios in pairs:
        for scenario in scenarios:
            named = any(
                token.lower() in gate.assignment_for(scenario).lower()
                for token in scenario.distinctive
            )
            assert named is (scenario.expected == "approve_once"), scenario.name


@pytest.mark.asyncio
async def test_live_judge_gate_reports_every_scenario_that_disagrees(monkeypatch, capsys):
    async def approve_everything(_prompt):
        return '{"verdict":"approve_once","reason":"looks fine"}'

    monkeypatch.setattr(gate, "codex_exec_json_runner", approve_everything)
    with pytest.raises(RuntimeError, match="live judge gate mismatches") as failure:
        await gate.main()

    reported = {entry["scenario"] for entry in json.loads(str(failure.value).split(": ", 1)[1])}
    assert reported == {
        scenario.name for scenario in gate.SCENARIOS if scenario.expected == "deny"
    }
    assert "FAIL" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_live_judge_gate_passes_when_every_verdict_matches(monkeypatch, capsys):
    expectations = {
        (scenario.command, gate.assignment_for(scenario)): scenario.expected
        for scenario in gate.SCENARIOS
    }
    assert len(expectations) == len(gate.SCENARIOS)

    async def obedient(prompt):
        case = json.loads(prompt)
        command = case["untrusted_evidence"]["request"]["command"]
        assignment = case["assignment"]["text"]
        return json.dumps(
            {"verdict": expectations[(command, assignment)], "reason": "per constitution"}
        )

    monkeypatch.setattr(gate, "codex_exec_json_runner", obedient)
    await gate.main()
    output = capsys.readouterr().out
    assert '"liveJudgeGate": "passed"' in output
    assert '"scenarios": 16' in output
    assert "FAIL" not in output


@pytest.mark.asyncio
async def test_live_judge_gate_surfaces_runner_failure(monkeypatch):
    async def runner(_prompt):
        raise RuntimeError("profile probe failed")

    monkeypatch.setattr(gate, "codex_exec_json_runner", runner)
    with pytest.raises(RuntimeError, match="profile probe failed"):
        await gate.main()


@pytest.mark.asyncio
async def test_session_scoped_grant_is_not_an_approval(monkeypatch):
    """The gate applies the deterministic ceiling, not only the raw verdict."""

    async def always_session(_prompt):
        return '{"verdict":"approve_session","reason":"convenient for the rest of the turn"}'

    monkeypatch.setattr(gate, "codex_exec_json_runner", always_session)
    with pytest.raises(RuntimeError, match="live judge gate mismatches") as failure:
        await gate.main()
    entries = json.loads(str(failure.value).split(": ", 1)[1])
    approvals = [
        entry for entry in entries
        if entry["scenario"] in {
            scenario.name for scenario in gate.SCENARIOS
            if scenario.expected == "approve_once"
        }
    ]
    assert approvals
    assert all(entry["verdict"] == "deny" for entry in approvals)


def test_exec_policy_rules_are_not_a_constitution():
    """The allow list lives in an execpolicy file; the constitution scan never reads it.

    The rules files deliberately name the mundane commands — that is their
    job — and the guard above must keep scanning only the constitutions, or
    the two kinds of file would collapse into one.
    """
    from codex_coordinator.execpolicy import ExecPolicy

    config = gate.load_operator_config()
    constitution_sources = set(_constitution_texts())
    rules_files = sorted((Path(gate.EXAMPLES) / "operator").glob("*.rules"))
    assert [path.name for path in rules_files] == ["inventory-app.rules", "inventory-report.rules"]

    for project, permissions in config.worker_permissions.items():
        policy = permissions.exec_policy
        assert policy is not None, project
        assert Path(policy.source).name == f"{project.name}.rules"
        assert policy.source not in constitution_sources
        assert policy.text not in _constitution_texts().values()
        # The rules name programs; the constitutions, by the test above, do not.
        programs = {rule.pattern[0][0] for rule in policy.rules}
        assert programs == {"sed", "rg", "python"}
        assert all(
            token not in text.lower()
            for token in ("prefix_rule", "sed -n")
            for text in _constitution_texts().values()
        )
    # And the scan is over constitutions only: a rules file is not a PolicyDocument.
    assert all(not source.endswith(".rules") for source in constitution_sources)
    for path in rules_files:
        ExecPolicy.from_text(path.read_text(), source=str(path))
