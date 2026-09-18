from pathlib import Path

import pytest

from codex_coordinator.installed_smoke import run


@pytest.mark.asyncio
async def test_installed_fixture_uses_two_existing_projects_without_writing(tmp_path: Path):
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir(); second.mkdir()
    await run(first, second)
    assert [list(path.iterdir()) for path in (first, second)] == [[], []]
