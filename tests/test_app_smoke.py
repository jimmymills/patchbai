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


@pytest.mark.asyncio
async def test_command_bar_message_round_trips_through_fake_orchestrator(tmp_path: Path):
    app = ModTuiApp(cwd=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("/")
        await pilot.press(*"hello world")
        await pilot.press("enter")
        await pilot.pause()

        # Both sides land in the orchestrator's transcript on disk.
        from mod_tui.persistence.transcript_store import OrchestratorTranscript
        entries = OrchestratorTranscript(cwd=tmp_path).read_all()
        roles = [e.role for e in entries]
        texts = [e.text for e in entries]
        assert roles == ["user", "orch"]
        assert texts == ["hello world", "I heard: hello world"]


@pytest.mark.asyncio
async def test_transcript_restored_on_relaunch(tmp_path: Path):
    # First run: send one message.
    app1 = ModTuiApp(cwd=tmp_path)
    async with app1.run_test() as pilot:
        await pilot.pause()
        await pilot.press("/")
        await pilot.press(*"persisted")
        await pilot.press("enter")
        await pilot.pause()

    # Second run: history should be loaded into the orchestrator.
    app2 = ModTuiApp(cwd=tmp_path)
    async with app2.run_test() as pilot:
        await pilot.pause()
        assert app2.orchestrator_history == [
            ("user", "persisted"),
            ("orch", "I heard: persisted"),
        ]
