from pathlib import Path

import pytest

from codex_coordinator.installed_smoke import FixtureClient, project, run


@pytest.mark.asyncio
async def test_installed_fixture_rejects_unsupported_thread_creation_field():
    with pytest.raises(RuntimeError, match="paginated thread creation"):
        await FixtureClient().call("thread/start", {"historyMode": "paginated"})


@pytest.mark.asyncio
async def test_installed_fixture_uses_existing_operator_projects_without_writing(tmp_path: Path):
    first = project(tmp_path, "first")
    second = project(tmp_path, "second")
    before = {
        path: (path / ".codex/config.toml").read_bytes()
        for path in (first, second)
    }
    await run(first, second)
    assert {
        path: (path / ".codex/config.toml").read_bytes()
        for path in (first, second)
    } == before


@pytest.mark.asyncio
async def test_installed_fixture_rejects_nested_operator_projects(tmp_path: Path):
    first = project(tmp_path, "first")
    nested = project(first, "nested")
    with pytest.raises(ValueError, match="unrelated project directories"):
        await run(first, nested)
