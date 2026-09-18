import importlib.util
from pathlib import Path


def test_generic_example_uses_named_projects_and_no_judge_surface():
    path = Path(__file__).resolve().parents[1] / "examples/generic_coordinator.py"
    text = path.read_text()
    assert "Coordinator.connect(config)" in text
    assert "Judge" not in text
    assert "resolve_approval" not in text
    spec = importlib.util.spec_from_file_location("generic_example", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
