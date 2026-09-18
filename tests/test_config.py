from pathlib import Path

import pytest

from codex_coordinator.config import OperatorConfig


def test_named_projects_are_canonical_and_overrides_apply(tmp_path):
    alpha = tmp_path / "alpha"; beta = tmp_path / "beta"
    alpha.mkdir(); beta.mkdir()
    config_file = tmp_path / "operator.toml"
    config_file.write_text(
        f'codex_command = "from-file"\nworker_model = "gpt-file"\n'
        f'[projects]\nalpha = "{alpha}"\nbeta = "{beta}"\n'
    )
    config = OperatorConfig.load(
        path=config_file,
        environ={"CODEX_COORDINATOR_WORKER_MODEL": "gpt-env"},
        overrides={"codex_command": "from-call"},
    )
    assert config.projects == {"alpha": alpha.resolve(), "beta": beta.resolve()}
    assert config.worker_model == "gpt-env"
    assert config.codex_command == "from-call"


@pytest.mark.parametrize("text, message", [
    ('[projects]\na = "relative"\n', "must be absolute"),
    ('[projects]\na = "/definitely/missing/coordinator-project"\n', "unavailable"),
    ('allowed_roots = []\n', "unsupported operator config fields"),
    ('approval_mode = "service"\n', "unsupported operator config fields"),
    ('item_capacity = 0\n', "integer of at least 1"),
])
def test_removed_or_invalid_configuration_is_rejected(tmp_path, text, message):
    path = tmp_path / "operator.toml"; path.write_text(text)
    with pytest.raises(ValueError, match=message):
        OperatorConfig.load(path=path, environ={})


def test_projects_environment_is_json(tmp_path):
    project = tmp_path / "project"; project.mkdir()
    config = OperatorConfig.load(environ={"CODEX_COORDINATOR_PROJECTS": f'{{"p":"{project}"}}'})
    assert config.projects["p"] == project.resolve()
    with pytest.raises(ValueError, match="must contain JSON"):
        OperatorConfig.load(environ={"CODEX_COORDINATOR_PROJECTS": "not-json"})
