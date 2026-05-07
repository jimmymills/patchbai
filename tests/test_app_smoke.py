from pathlib import Path

import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock
from textual.app import App

from mod_tui.agents.fake_sdk_adapter import FakeSDKAdapter
from mod_tui.agents.manager import AgentManager
from mod_tui.app import ModTuiApp
from mod_tui.events import EventBus
from mod_tui.orchestrator.session import OrchestratorSession
from mod_tui.widgets.agent_table import AgentTable
from mod_tui.widgets.chrome import CommandBar, StatusBar
from mod_tui.widgets.orchestrator_chat import OrchestratorChat
from mod_tui.widgets.placeholders import ActivityFeed


def _ok_script() -> list:
    return [
        AssistantMessage(content=[TextBlock(text="acknowledged")], model="fake-model"),
        ResultMessage(
            subtype="success",
            duration_ms=1, duration_api_ms=1, is_error=False, num_turns=1,
            session_id="fake", total_cost_usd=0.0,
            usage={"input_tokens": 1, "output_tokens": 1},
            result="acknowledged",
        ),
    ]


def _build_test_app(tmp_path: Path) -> ModTuiApp:
    bus = EventBus()
    manager = AgentManager(
        cwd=tmp_path,
        bus=bus,
        adapter_factory=lambda: FakeSDKAdapter(scripts=[_ok_script()]),
    )
    orchestrator = OrchestratorSession(
        cwd=tmp_path,
        bus=bus,
        manager=manager,
        adapter=FakeSDKAdapter(scripts=[_ok_script()]),
    )
    app = ModTuiApp(cwd=tmp_path, manager=manager, orchestrator=orchestrator)
    app.event_bus = bus  # share
    return app


@pytest.mark.asyncio
async def test_default_dashboard_mounts_three_panels(tmp_path: Path):
    app = _build_test_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.query_one(CommandBar) is not None
        assert app.query_one(StatusBar) is not None
        assert app.query_one(OrchestratorChat) is not None
        assert app.query_one(AgentTable) is not None
        assert app.query_one(ActivityFeed) is not None


@pytest.mark.asyncio
async def test_slash_focuses_command_bar(tmp_path: Path):
    app = _build_test_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("/")
        cmd = app.query_one(CommandBar)
        assert cmd.query_one("#cmd-input").has_focus


@pytest.mark.asyncio
async def test_slash_types_into_focused_input(tmp_path: Path):
    """Regression: '/' must reach a focused Input as text, not steal focus.

    Previously the binding was priority=True, so the keypress fired the
    `focus_command_bar` action before the orchestrator chat input could
    consume it as a character.
    """
    from textual.widgets import Input

    app = _build_test_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        chat_input = app.query_one(OrchestratorChat).query_one(Input)
        chat_input.focus()
        await pilot.pause()
        await pilot.press("/")
        await pilot.press(*"clear")
        # The character reached the input as text instead of triggering the
        # command-bar focus binding.
        assert chat_input.value == "/clear"
        assert chat_input.has_focus


@pytest.mark.asyncio
async def test_ctrl_c_interrupts_orchestrator_from_input(tmp_path: Path):
    """ctrl+c on the orchestrator input must call orchestrator.interrupt(),
    not be eaten by Textual's default driver handling (which quits)."""
    from textual.widgets import Input

    app = _build_test_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        calls: list[str] = []

        async def _spy() -> None:
            calls.append("interrupt")

        app.orchestrator.interrupt = _spy  # type: ignore[method-assign]

        chat_input = app.query_one(OrchestratorChat).query_one(Input)
        chat_input.focus()
        await pilot.pause()
        await pilot.press("ctrl+c")
        await pilot.pause()

        assert calls == ["interrupt"]
        # Input still focused — binding didn't blur or quit the app.
        assert chat_input.has_focus


@pytest.mark.asyncio
async def test_layout_persists_across_app_runs(tmp_path: Path):
    # First run.
    app1 = _build_test_app(tmp_path)
    async with app1.run_test() as pilot:
        await pilot.pause()
        # The new persistence format is workspace.json (not the legacy layout.json).
        assert (tmp_path / ".mod_tui" / "workspace.json").exists()

    # Second run: same cwd, verify layout restored.
    app2 = _build_test_app(tmp_path)
    async with app2.run_test() as pilot:
        await pilot.pause()
        assert app2._active_layout() is not None
        assert app2.query_one(OrchestratorChat) is not None


@pytest.mark.asyncio
async def test_command_bar_message_round_trips_through_real_orchestrator(tmp_path: Path):
    app = _build_test_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("/")
        await pilot.press(*"hello world")
        await pilot.press("enter")
        await pilot.pause()
        await app.orchestrator.wait_idle()

        from mod_tui.persistence.transcript_store import OrchestratorTranscript
        entries = OrchestratorTranscript(cwd=tmp_path).read_all()
        roles = [e.role for e in entries]
        texts = [e.text for e in entries]
        assert "user" in roles and "assistant" in roles
        assert "hello world" in texts
        assert "acknowledged" in texts


@pytest.mark.asyncio
async def test_orchestrator_chat_uses_rich_transcript(tmp_path):
    """OrchestratorChat composes a RichTranscript for the 'orchestrator' agent."""
    from mod_tui.widgets.orchestrator_chat import OrchestratorChat
    from mod_tui.widgets.rich_transcript import RichTranscript
    from mod_tui.events import EventBus

    class _Host(App):
        event_bus = EventBus()
        def compose(self):
            yield OrchestratorChat()

    app = _Host()
    app.cwd = tmp_path
    async with app.run_test() as pilot:
        await pilot.pause()
        chat = app.query_one(OrchestratorChat)
        rt = chat.query_one(RichTranscript)
        assert rt.agent_id == "orchestrator"
