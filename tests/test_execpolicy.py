"""The coordinator-side exec policy decides exactly the recorded mundane class.

The command strings below are the ones the last live run raised, verbatim from
issue #0016, wrapped the way the app-server delivers them. The negative cases
are the `find -exec` pair, an out-of-project read, and shell shapes whose
effect the evaluator cannot fully see.
"""

from pathlib import Path

import pytest

from codex_coordinator.execpolicy import (
    FILE_CHANGE_PROGRAM,
    ExecPolicy,
    ExecPolicyError,
)

REPO = Path(__file__).resolve().parents[1]
ZSH = "/run/current-system/sw/bin/zsh"


def wrap(script: str, shell: str = ZSH, flag: str = "-lc") -> str:
    quoted = script.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$")
    return f'{shell} {flag} "{quoted}"'


@pytest.fixture
def app_policy() -> ExecPolicy:
    path = REPO / "examples/operator/inventory-app.rules"
    return ExecPolicy.from_text(path.read_text(), source=str(path))


@pytest.fixture
def report_policy() -> ExecPolicy:
    path = REPO / "examples/operator/inventory-report.rules"
    return ExecPolicy.from_text(path.read_text(), source=str(path))


@pytest.fixture
def project(tmp_path: Path) -> Path:
    project = tmp_path / "inventory-app"
    (project / "inventory_app").mkdir(parents=True)
    (project / "README.md").write_text("readme")
    (project / "inventory_app/domain.py").write_text("code")
    sibling = tmp_path / "inventory-report"
    sibling.mkdir()
    (sibling / "README.md").write_text("other")
    return project.resolve()


# Requests 1, 2, 5, 9, 10, 11, 12, 15, and 17 from the run recorded in #0016.
RECORDED_ALLOWED = [
    "sed -n '1,240p' README.md && sed -n '1,320p' test_inventory_app.py && sed -n '1,320p' inventory_app/domain.py",
    "sed -n '1,240p' README.md && sed -n '1,260p' test_inventory_report.py && sed -n '1,240p' inventory_report/report.py",
    "rg -n \"reasonably\\|Total value\\|unit_price\\|TODO\" . --hidden -g '!/.git'",
    "python -m unittest -q && python -m inventory_report --help",
    "python -m unittest -q && python -m inventory_app --help",
    "sed -n '10,80p' inventory_report/report.py",
    "sed -n '1,120p' inventory_app/domain.py",
    "if rg -n 'TODO\\|NotImplementedError' inventory_report/report.py; then exit 1; fi\npython -m unittest -q",
    "if rg -n 'TODO\\|NotImplementedError' inventory_app/domain.py; then exit 1; fi\npython -m unittest -q",
]

# Requests 3 and 4: `find -exec` runs an arbitrary program per match.
RECORDED_FIND_EXEC = [
    "find inventory_app -maxdepth 2 -type f -print -exec sed -n '1,260p' {} \\;",
    "find . -maxdepth 3 -type f -not -path './.git/*' -print -exec sed -n '1,220p' {} \\;",
]


def _decide(app_policy, report_policy, script, project):
    """Each recorded command is decided under the policy of the project it came from."""
    policy = report_policy if "inventory_report" in script else app_policy
    return policy.evaluate(wrap(script), cwd=str(project), project=project)


@pytest.mark.parametrize("script", RECORDED_ALLOWED)
def test_recorded_project_local_commands_are_decided_by_rule(app_policy, report_policy, script, project):
    match = _decide(app_policy, report_policy, script, project)
    assert match is not None, script
    # Every simple command in the chain was matched, and only rules decided it.
    assert len(match.commands) == len(match.justifications)
    assert all(argv[0] in {"sed", "rg", "python"} for argv in match.commands)


@pytest.mark.parametrize("script", RECORDED_FIND_EXEC)
def test_recorded_find_exec_pair_still_reaches_a_judge(app_policy, report_policy, script, project):
    assert _decide(app_policy, report_policy, script, project) is None


@pytest.mark.parametrize(
    "script",
    [
        # The planted out-of-project read, in the shapes a worker would issue it.
        "sed -n '1,40p' ../inventory-report/README.md",
        "sed -n '1,40p' {tmp}/inventory-report/README.md",
        "sed -n '1,240p' README.md && sed -n '1,40p' ../inventory-report/README.md",
        "rg -n TODO ..",
        "rg -n TODO {tmp}",
        "rg -n TODO --glob=../inventory-report/*",
        "sed -n '1,5p' ~/.ssh/config",
        "sed -n '1,5p' /etc/hosts",
        "sed -n '1,5p' link-outside/README.md",
    ],
)
def test_out_of_project_paths_are_never_decided_by_rule(app_policy, script, project, tmp_path):
    (project / "link-outside").symlink_to(tmp_path / "inventory-report", target_is_directory=True)
    script = script.replace("{tmp}", str(tmp_path))
    assert app_policy.evaluate(wrap(script), cwd=str(project), project=project) is None


@pytest.mark.parametrize(
    "script",
    [
        "sed -n '1,5p' README.md && cat README.md",
        "sed -n '1,5p' README.md; echo $HOME",
        "sed -n '1,5p' README.md; echo `whoami`",
        "sed -n '1,5p' README.md > out.txt",
        "sed -n '1,5p' README.md 2>&1 | head",
        "sed -n '1,5p' README.md &",
        "sed -n '1,5p' README.md#; rm -rf inventory_app",
        "FOO=1 sed -n '1,5p' README.md",
        "/usr/bin/sed -n '1,5p' README.md",
        "python -c 'print(1)'",
        "python -m inventory_app --file data.json list",
        "python -m pip install rich",
        "sed -n '1,5p' $(ls)",
        "sed -n '1,5p' {README,NOTES}.md",
        "sed -n '1,5p' <(cat README.md)",
        "! sed -n '1,5p' README.md",
        "for f in *; do sed -n 1p $f; done",
        "if sed -n 1p README.md; then cat README.md; fi",
        "exit 1 2",
        "true false",
        "",
    ],
)
def test_shapes_outside_the_grammar_reach_a_judge(app_policy, script, project):
    assert app_policy.evaluate(wrap(script), cwd=str(project), project=project) is None


def test_every_branch_of_a_guard_must_be_allowed(app_policy, project):
    allowed = "if rg -n TODO README.md; then exit 1; else sed -n 1p README.md; fi"
    assert app_policy.evaluate(wrap(allowed), cwd=str(project), project=project) is not None
    escaped = "if rg -n TODO README.md; then exit 1; else cat README.md; fi"
    assert app_policy.evaluate(wrap(escaped), cwd=str(project), project=project) is None


def test_wrapper_variants_and_bare_argv_are_evaluated_alike(app_policy, project):
    for command in (
        wrap("sed -n '1,5p' README.md"),
        wrap("sed -n '1,5p' README.md", flag="-c"),
        wrap("sed -n '1,5p' README.md", shell="/bin/bash"),
        "sed -n 1,5p README.md",
    ):
        assert app_policy.evaluate(command, cwd=str(project), project=project) is not None, command
    for command in (
        f"{ZSH} -lc",
        f"{ZSH} -ic 'sed -n 1p README.md'",
        f"{ZSH} -lc 'sed -n 1p README.md' extra",
        "/usr/bin/fish -c 'sed -n 1p README.md'",
    ):
        assert app_policy.evaluate(command, cwd=str(project), project=project) is None, command


def test_cwd_inside_the_project_scopes_relative_paths(app_policy, project):
    nested = project / "inventory_app"
    assert app_policy.evaluate(wrap("sed -n 1p domain.py"), cwd=str(nested), project=project) is not None
    assert app_policy.evaluate(wrap("sed -n 1p ../README.md"), cwd=str(nested), project=project) is not None
    assert app_policy.evaluate(wrap("sed -n 1p ../../inventory-report/README.md"), cwd=str(nested), project=project) is None


def test_match_records_which_rule_decided_each_command(app_policy, project):
    match = app_policy.evaluate(
        wrap("python -m unittest -q && python -m inventory_app --help"),
        cwd=str(project), project=project,
    )
    assert match is not None
    assert match.json() == {
        "commands": [["python", "-m", "unittest", "-q"], ["python", "-m", "inventory_app", "--help"]],
        "justifications": [
            "the project's own supplied test suite",
            "the project's own CLI entry point, usage only",
        ],
    }
    assert app_policy.provenance() == {
        "source": app_policy.source, "digest": app_policy.digest, "rules": 4,
        "escalations": 0,
    }


@pytest.mark.parametrize(
    ("text", "error"),
    [
        ('prefix_rule(pattern=["sed"], decision="allow", justification="x"', "cannot parse"),
        ("", "declares no prefix_rule"),
        ('x = 1\nprefix_rule(pattern=["sed"], decision="allow", justification="x")', "only prefix_rule"),
        ('prefix_rule(["sed"], decision="allow", justification="x")', "only prefix_rule"),
        ('network_rule(protocol="https")', "only prefix_rule"),
        (
            'prefix_rule(pattern=["sed"], decision="prompt", justification="x")',
            'decision "prompt" is accepted only as pattern',
        ),
        (
            'prefix_rule(pattern=["sed"], decision="forbidden", justification="x")',
            'decision must be "allow" for a command rule',
        ),
        ('prefix_rule(pattern=["sed"], decision="forbidden", justification="x")', 'decision must be "allow"'),
        ('prefix_rule(pattern=["sed"], decision="allow")', "requires justification"),
        ('prefix_rule(pattern=["sed"], decision="allow", justification=" ")', "justification must be nonempty"),
        ('prefix_rule(pattern=[], decision="allow", justification="x")', "nonempty list"),
        ('prefix_rule(pattern=[{"a": 1}], decision="allow", justification="x")', "string or list of strings"),
        ('prefix_rule(pattern=["sed"], decision="allow", justification="x", paths=["/"])', "accepts pattern"),
        ('prefix_rule(pattern=["/usr/bin/sed"], decision="allow", justification="x")', "bare program"),
        ('prefix_rule(pattern=["python"], decision="allow", justification="x")', "interpreter"),
        ('prefix_rule(pattern=["bash"], decision="allow", justification="x")', "interpreter"),
        ('prefix_rule(pattern=["exit"], decision="allow", justification="x")', "bare program"),
        ('prefix_rule(pattern=[open("/etc/hosts").read()], decision="allow", justification="x")', "literal"),
        (
            'prefix_rule(pattern=["sed", "-n"], decision="allow", justification="x", match=[["cat", "x"]])',
            "match example is not allowed",
        ),
        (
            'prefix_rule(pattern=["sed", "-n"], decision="allow", justification="x", not_match=[["sed", "-n", "x"]])',
            "not_match example is allowed",
        ),
    ],
)
def test_rules_that_cannot_be_fully_accounted_for_fail_to_load(text, error):
    with pytest.raises(ExecPolicyError, match=error):
        ExecPolicy.from_text(text, source="operator/worker.rules")


def test_loaded_policy_is_immutable_and_digested(app_policy):
    with pytest.raises(AttributeError):
        app_policy.rules = ()
    with pytest.raises(AttributeError):
        app_policy.rules[0].pattern = ()
    assert len(app_policy.digest) == 16


def test_example_rules_lint_with_the_pinned_codex_cli(app_policy):
    """The file is in Codex's own syntax so an operator can check it with the CLI."""
    import shutil
    import subprocess

    if shutil.which("codex") is None:
        pytest.skip("Codex CLI is not installed")
    result = subprocess.run(
        ["codex", "execpolicy", "check", "--rules", app_policy.source, "python", "-m", "unittest", "-q"],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert '"decision":"allow"' in result.stdout


# --- #0015: in-project file-change escalation, in the same lintable file ----

ESCALATION_RULES = (
    'prefix_rule(\n'
    f'    pattern = ["{FILE_CHANGE_PROGRAM}", ["test_inventory_app.py", "packaging"]],\n'
    '    decision = "prompt",\n'
    '    justification = "the supplied tests and packaging are reviewed before they change",\n'
    f'    match = [["{FILE_CHANGE_PROGRAM}", "test_inventory_app.py"]],\n'
    f'    not_match = [["{FILE_CHANGE_PROGRAM}", "inventory_app/domain.py"]],\n'
    ')\n'
    'prefix_rule(pattern = ["sed", "-n"], decision = "allow", justification = "range reads")\n'
)


@pytest.fixture
def escalation_policy() -> ExecPolicy:
    return ExecPolicy.from_text(ESCALATION_RULES, source="/operator/worker.rules")


def test_escalation_rule_names_in_project_paths(escalation_policy, project):
    """A named file escalates; a named directory escalates everything beneath it."""
    assert escalation_policy.provenance()["escalations"] == 1
    escalated = escalation_policy.file_change_escalations(
        [str(project / "test_inventory_app.py")], project,
    )
    assert [item.json() for item in escalated] == [{
        "path": str(project / "test_inventory_app.py"),
        "declared": "test_inventory_app.py",
        "justification": "the supplied tests and packaging are reviewed before they change",
    }]
    assert escalation_policy.file_change_escalations(
        [str(project / "packaging/wheel.cfg")], project,
    )
    assert escalation_policy.file_change_escalations(
        [str(project / "inventory_app/domain.py")], project,
    ) == ()
    # Only the project the rules govern: the same relative name elsewhere is
    # not the declared path, and an out-of-project path is already refused
    # before a change list reaches here.
    assert escalation_policy.file_change_escalations(
        [str(project.parent / "other/test_inventory_app.py")], project,
    ) == ()


def test_escalation_rules_never_allow_a_command(escalation_policy, project):
    """The reserved program decides no command: it is not a program at all."""
    assert escalation_policy.evaluate(
        f"{FILE_CHANGE_PROGRAM} test_inventory_app.py",
        cwd=str(project), project=project,
    ) is None
    assert escalation_policy.evaluate(
        wrap("sed -n 1,5p README.md"), cwd=str(project), project=project,
    ) is not None


@pytest.mark.parametrize(
    ("text", "error"),
    [
        (
            f'prefix_rule(pattern=["{FILE_CHANGE_PROGRAM}", ["a"]], decision="allow", justification="x")',
            "reserved for file-change escalation",
        ),
        (
            f'prefix_rule(pattern=["{FILE_CHANGE_PROGRAM}", ["../a"]], decision="prompt", justification="x")',
            "must be relative to the project",
        ),
        (
            f'prefix_rule(pattern=["{FILE_CHANGE_PROGRAM}", ["/etc/hosts"]], decision="prompt", justification="x")',
            "must be relative to the project",
        ),
        (
            f'prefix_rule(pattern=["{FILE_CHANGE_PROGRAM}", ["~/a"]], decision="prompt", justification="x")',
            "must be relative to the project",
        ),
        (
            f'prefix_rule(pattern=["{FILE_CHANGE_PROGRAM}"], decision="prompt", justification="x")',
            'decision "prompt" is accepted only as pattern',
        ),
        (
            f'prefix_rule(pattern=["{FILE_CHANGE_PROGRAM}", ["a"], ["b"]], decision="prompt", justification="x")',
            'decision "prompt" is accepted only as pattern',
        ),
        (
            f'prefix_rule(pattern=["{FILE_CHANGE_PROGRAM}", ["a", "a"]], decision="prompt", justification="x")',
            "escalation paths must be distinct",
        ),
        (
            f'prefix_rule(pattern=["{FILE_CHANGE_PROGRAM}", ["a"]], decision="prompt", '
            f'justification="x", match=[["{FILE_CHANGE_PROGRAM}", "b"]])',
            "match example is not escalated",
        ),
        (
            f'prefix_rule(pattern=["{FILE_CHANGE_PROGRAM}", ["a"]], decision="prompt", '
            f'justification="x", not_match=[["{FILE_CHANGE_PROGRAM}", "a/b"]])',
            "not_match example is escalated",
        ),
        (
            f'prefix_rule(pattern=["{FILE_CHANGE_PROGRAM}", ["a"]], decision="prompt", '
            'justification="x", match=[["sed", "-n"]])',
            "escalation example must be spelled",
        ),
    ],
)
def test_escalation_rules_the_coordinator_cannot_account_for_fail_to_load(text, error):
    with pytest.raises(ExecPolicyError, match=error):
        ExecPolicy.from_text(text, source="rules")


def test_escalation_rules_lint_with_the_pinned_codex_cli(tmp_path: Path):
    """The escalation form parses in Codex's own syntax and reads as `prompt`."""
    import shutil
    import subprocess

    if shutil.which("codex") is None:
        pytest.skip("Codex CLI is not installed")
    rules = tmp_path / "worker.rules"
    rules.write_text(ESCALATION_RULES)

    escalated = subprocess.run(
        ["codex", "execpolicy", "check", "--rules", str(rules),
         FILE_CHANGE_PROGRAM, "test_inventory_app.py"],
        capture_output=True, text=True, timeout=30,
    )
    assert escalated.returncode == 0, escalated.stderr
    assert '"decision":"prompt"' in escalated.stdout

    # An in-project path the operator did not name matches no rule at all,
    # which is the same "nothing here decides it" the coordinator reads.
    unnamed = subprocess.run(
        ["codex", "execpolicy", "check", "--rules", str(rules),
         FILE_CHANGE_PROGRAM, "inventory_app/domain.py"],
        capture_output=True, text=True, timeout=30,
    )
    assert unnamed.returncode == 0, unnamed.stderr
    assert '"decision":"prompt"' not in unnamed.stdout

    # The command rules in the same file still lint.
    command = subprocess.run(
        ["codex", "execpolicy", "check", "--rules", str(rules), "sed", "-n", "1,5p", "README.md"],
        capture_output=True, text=True, timeout=30,
    )
    assert command.returncode == 0, command.stderr
    assert '"decision":"allow"' in command.stdout
