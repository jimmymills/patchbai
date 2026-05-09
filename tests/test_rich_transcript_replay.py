import pytest
from textual.app import App

from patchfeld.events import EventBus
from patchfeld.persistence.transcript_store import AgentTranscript as Store, TranscriptEntry
from patchfeld.widgets.rich_transcript import RichTranscript, _TurnContainer


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
    from patchfeld.widgets.rich_transcript import _ToolCall

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


@pytest.mark.asyncio
async def test_replay_does_not_start_spinner_on_completed_tool(tmp_path):
    """Replay must not show the spinner on tool foldables whose result
    already arrived. Regression: Textual defers mount, so on_mount used to
    fire after attach_result and unconditionally start the timer."""
    from patchfeld.widgets.rich_transcript import _ToolCall, _SPINNER_FRAMES

    store = Store(cwd=tmp_path, agent_id="a1")
    store.append(TranscriptEntry(role="user", text="go"))
    store.append(TranscriptEntry(role="tool_use", text="[bash] {'cmd': 'ls'}",
                                 tool_id="t1", tool_name="bash"))
    store.append(TranscriptEntry(role="tool_result", text="bin\nlib\n",
                                 tool_id="t1"))

    bus = EventBus()
    app = _HostApp(bus, "a1")
    app.cwd = tmp_path
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause(0.2)  # let any spinner timer tick a few times

        widget = app.query_one(RichTranscript)
        tools = list(widget.query(_ToolCall))
        assert len(tools) == 1
        tool = tools[0]
        assert tool.collapsed is True
        # The spinner must not be running on a replayed, already-done tool.
        assert tool._spinner_timer is None
        # Title shows the success marker, not a spinner glyph.
        assert tool.title.startswith("✓")
        assert tool.title[0] not in _SPINNER_FRAMES


@pytest.mark.asyncio
async def test_replay_tool_result_with_brackets_does_not_crash(tmp_path):
    # Regression: tool results that contain "[" followed by chars other than
    # [a-z#/@] (e.g. cat -n output "[\n", JSON "[{") used to crash on replay
    # because textual.markup.escape passed them through unescaped, and the
    # Collapsible title's markup parser then choked on the truncation "…".
    from patchfeld.widgets.rich_transcript import _ToolCall

    payload = (
        '820\t            "tabs": [\n'
        '821\t                {**t.model_dump(mode="json"), '
        '"layout": spec.model_dump(mode="json")}\n'
        '822\t                if t.id == tab_id else t.model_dump(mode="json")\n'
    )

    store = Store(cwd=tmp_path, agent_id="a1")
    store.append(TranscriptEntry(role="user", text="go"))
    store.append(TranscriptEntry(role="tool_use", text="{'file_path': 'x'}",
                                 tool_id="t1", tool_name="Read"))
    store.append(TranscriptEntry(role="tool_result", text=payload,
                                 tool_id="t1"))

    bus = EventBus()
    app = _HostApp(bus, "a1")
    app.cwd = tmp_path
    async with app.run_test() as pilot:
        await pilot.pause()
        widget = app.query_one(RichTranscript)
        tools = list(widget.query(_ToolCall))
        assert len(tools) == 1
        # If the bug is back, mounting throws MarkupError before we reach here.
        assert tools[0].title.startswith("✓")


@pytest.mark.asyncio
async def test_replay_does_not_start_spinner_on_completed_thinking(tmp_path):
    """Same regression for thinking groups closed by rolling-turn-close."""
    from patchfeld.widgets.rich_transcript import _ThinkingGroup, _SPINNER_FRAMES

    store = Store(cwd=tmp_path, agent_id="a1")
    # Two turns so the first one's thinking group closes via _open_turn rolling close.
    store.append(TranscriptEntry(role="user", text="q1"))
    store.append(TranscriptEntry(role="thinking", text="planning..."))
    store.append(TranscriptEntry(role="assistant", text="a1"))
    store.append(TranscriptEntry(role="user", text="q2"))
    store.append(TranscriptEntry(role="assistant", text="a2"))

    bus = EventBus()
    app = _HostApp(bus, "a1")
    app.cwd = tmp_path
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause(0.2)

        widget = app.query_one(RichTranscript)
        groups = list(widget.query(_ThinkingGroup))
        assert len(groups) == 1
        group = groups[0]
        assert group.collapsed is True
        assert group._spinner_timer is None
        assert "Thought for" in group.title
        assert group.title[0] not in _SPINNER_FRAMES


@pytest.mark.asyncio
async def test_replay_runs_in_a_worker_not_inline(tmp_path):
    """on_mount must drive replay from a worker — not inline — so a
    transcript with hundreds of entries doesn't stall the UI on tab
    open. We observe two signals:
      * a named worker is spawned for replay
      * a long history (100 turns) replays completely and correctly
    """
    store = Store(cwd=tmp_path, agent_id="a1")
    n_turns = 100
    for i in range(n_turns):
        store.append(TranscriptEntry(role="user", text=f"q{i}"))
        store.append(TranscriptEntry(role="assistant", text=f"a{i}"))

    bus = EventBus()
    app = _HostApp(bus, "a1")
    app.cwd = tmp_path
    async with app.run_test() as pilot:
        widget = app.query_one(RichTranscript)
        # Replay was driven by a worker (tracked even after completion).
        assert widget._replay_worker is not None, (
            "on_mount did not spawn a replay worker — replay would block the loop"
        )

        # Drain and verify all turns landed.
        await pilot.pause(2.0)
        full = len(widget.query(_TurnContainer))
        assert full == n_turns, f"expected all {n_turns} turns, got {full}"
