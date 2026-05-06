from pathlib import Path

import pytest

from mod_tui.app import ModTuiApp
from mod_tui.widgets.chrome import CommandBar, StatusBar
from mod_tui.widgets.orchestrator_chat import OrchestratorChat
from mod_tui.widgets.placeholders import ActivityFeed, AgentTable


@pytest.mark.asyncio
async def test_default_dashboard_mounts_three_panels(tmp_path: Path):
    app = ModTuiApp(cwd=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.query_one(CommandBar) is not None
        assert app.query_one(StatusBar) is not None
        assert app.query_one(OrchestratorChat) is not None
        assert app.query_one(AgentTable) is not None
        assert app.query_one(ActivityFeed) is not None


@pytest.mark.asyncio
async def test_slash_focuses_command_bar(tmp_path: Path):
    app = ModTuiApp(cwd=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("/")
        cmd = app.query_one(CommandBar)
        assert cmd.query_one("#cmd-input").has_focus


@pytest.mark.asyncio
async def test_layout_persists_across_app_runs(tmp_path: Path):
    # First run: launch, mount default, save.
    app1 = ModTuiApp(cwd=tmp_path)
    async with app1.run_test() as pilot:
        await pilot.pause()
        assert (tmp_path / ".mod_tui" / "layout.json").exists()

    # Second run in same cwd: should load the saved layout.
    app2 = ModTuiApp(cwd=tmp_path)
    async with app2.run_test() as pilot:
        await pilot.pause()
        assert app2._current_spec is not None
        # Default dashboard has the orch panel — it must still be there.
        assert app2.query_one(OrchestratorChat) is not None
