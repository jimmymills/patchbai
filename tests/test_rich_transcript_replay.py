import pytest
from textual.app import App

from mod_tui.events import EventBus
from mod_tui.persistence.transcript_store import AgentTranscript as Store, TranscriptEntry
from mod_tui.widgets.rich_transcript import RichTranscript, _TurnContainer


class _HostApp(App):
    def __init__(self, bus: EventBus, agent_id: str) -> None:
        super().__init__()
        self.event_bus = bus
        self._agent_id = agent_id

    def compose(self):
        yield RichTranscript(agent_id=self._agent_id, event_bus=self.event_bus)


@pytest.mark.asyncio
async def test_replay_marks_all_turns_done(tmp_path):
    store = Store(cwd=tmp_path, agent_id="a1")
    store.append(TranscriptEntry(role="user", text="q1"))
    store.append(TranscriptEntry(role="assistant", text="a1"))
    store.append(TranscriptEntry(role="user", text="q2"))
    store.append(TranscriptEntry(role="assistant", text="a2"))

    bus = EventBus()
    app = _HostApp(bus, "a1")
    app.cwd = tmp_path
    async with app.run_test() as pilot:
        await pilot.pause()
        widget = app.query_one(RichTranscript)
        turns = list(widget.query(_TurnContainer))
        assert len(turns) == 2
        for t in turns:
            assert t.has_class("turn-done"), f"turn not marked done: {t}"
            assert not t.has_class("turn-running")


@pytest.mark.asyncio
async def test_old_transcript_without_tool_id_still_pairs(tmp_path):
    """tool_result without tool_id falls back to most-recent-pending pairing."""
    from mod_tui.widgets.rich_transcript import _ToolCall

    store = Store(cwd=tmp_path, agent_id="a1")
    store.append(TranscriptEntry(role="user", text="go"))
    # Old-format records have no tool_id at all.
    store.append(TranscriptEntry(role="tool_use", text="[bash] {'cmd': 'ls'}"))
    store.append(TranscriptEntry(role="tool_result", text="bin\nlib\n"))

    bus = EventBus()
    app = _HostApp(bus, "a1")
    app.cwd = tmp_path
    async with app.run_test() as pilot:
        await pilot.pause()
        widget = app.query_one(RichTranscript)
        tools = list(widget.query(_ToolCall))
        assert len(tools) == 1
        # The result attached to it (no orphan was mounted as a Static).
        from textual.widgets import Static
        body = "\n".join(str(s.content) for s in tools[0].query(Static))
        assert "bin" in body
