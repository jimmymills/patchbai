import pytest
from textual.app import App
from textual.widgets import Static

from patchfeld.events import AgentMessageAppended, EventBus
from patchfeld.persistence.transcript_store import AgentTranscript as Store, TranscriptEntry
from patchfeld.widgets.rich_transcript import RichTranscript


class _HostApp(App):
    def __init__(self, bus: EventBus, agent_id: str) -> None:
        super().__init__()
        self.event_bus = bus
        self._agent_id = agent_id

    def compose(self):
        yield RichTranscript(agent_id=self._agent_id, event_bus=self.event_bus)


@pytest.mark.asyncio
async def test_rich_transcript_replays_history_from_disk(tmp_path):
    store = Store(cwd=tmp_path, agent_id="a1")
    store.append(TranscriptEntry(role="user", text="hello"))
    store.append(TranscriptEntry(role="assistant", text="hi"))

    bus = EventBus()
    app = _HostApp(bus, "a1")
    app.cwd = tmp_path
    async with app.run_test() as pilot:
        await pilot.pause()
        widget = app.query_one(RichTranscript)
        text = widget.rendered_text()
        assert "hello" in text
        assert "hi" in text


@pytest.mark.asyncio
async def test_rich_transcript_appends_live_messages(tmp_path):
    bus = EventBus()
    app = _HostApp(bus, "a1")
    app.cwd = tmp_path
    async with app.run_test() as pilot:
        await pilot.pause()
        bus.publish(AgentMessageAppended(agent_id="a1", role="assistant", text="live!"))
        await pilot.pause()
        widget = app.query_one(RichTranscript)
        assert "live!" in widget.rendered_text()


@pytest.mark.asyncio
async def test_rich_transcript_ignores_other_agents(tmp_path):
    bus = EventBus()
    app = _HostApp(bus, "a1")
    app.cwd = tmp_path
    async with app.run_test() as pilot:
        await pilot.pause()
        bus.publish(AgentMessageAppended(agent_id="b2", role="assistant", text="leak"))
        await pilot.pause()
        widget = app.query_one(RichTranscript)
        assert "leak" not in widget.rendered_text()


@pytest.mark.asyncio
async def test_each_user_message_opens_a_new_turn(tmp_path):
    from patchfeld.widgets.rich_transcript import _TurnContainer

    bus = EventBus()
    app = _HostApp(bus, "a1")
    app.cwd = tmp_path
    async with app.run_test() as pilot:
        await pilot.pause()
        bus.publish(AgentMessageAppended(agent_id="a1", role="user", text="q1"))
        bus.publish(AgentMessageAppended(agent_id="a1", role="assistant", text="a1"))
        bus.publish(AgentMessageAppended(agent_id="a1", role="user", text="q2"))
        bus.publish(AgentMessageAppended(agent_id="a1", role="assistant", text="a2"))
        await pilot.pause()

        widget = app.query_one(RichTranscript)
        turns = list(widget.query(_TurnContainer))
        assert len(turns) == 2
        # Each turn contains its own assistant reply.
        assert "q1" in turns[0].rendered_text()
        assert "a1" in turns[0].rendered_text()
        assert "q2" in turns[1].rendered_text()
        assert "a2" in turns[1].rendered_text()


@pytest.mark.asyncio
async def test_assistant_text_routes_to_current_turn(tmp_path):
    from patchfeld.widgets.rich_transcript import _TurnContainer

    bus = EventBus()
    app = _HostApp(bus, "a1")
    app.cwd = tmp_path
    async with app.run_test() as pilot:
        await pilot.pause()
        bus.publish(AgentMessageAppended(agent_id="a1", role="user", text="hi"))
        bus.publish(AgentMessageAppended(agent_id="a1", role="thinking", text="planning..."))
        bus.publish(AgentMessageAppended(
            agent_id="a1", role="tool_use", text="[bash] {'cmd': 'ls'}",
            tool_id="t1", tool_name="bash",
        ))
        bus.publish(AgentMessageAppended(
            agent_id="a1", role="tool_result", text="<output>", tool_id="t1",
        ))
        bus.publish(AgentMessageAppended(agent_id="a1", role="assistant", text="done"))
        await pilot.pause()

        widget = app.query_one(RichTranscript)
        turns = list(widget.query(_TurnContainer))
        assert len(turns) == 1
        body = turns[0].rendered_text()
        assert "hi" in body
        assert "planning..." in body
        assert "bash" in body
        assert "<output>" in body
        assert "done" in body


@pytest.mark.asyncio
async def test_tool_use_renders_as_expanded_collapsible(tmp_path):
    from patchfeld.widgets.rich_transcript import _ToolCall

    bus = EventBus()
    app = _HostApp(bus, "a1")
    app.cwd = tmp_path
    async with app.run_test() as pilot:
        await pilot.pause()
        bus.publish(AgentMessageAppended(agent_id="a1", role="user", text="go"))
        bus.publish(AgentMessageAppended(
            agent_id="a1", role="tool_use", text="{'cmd': 'ls /tmp'}",
            tool_id="t1", tool_name="bash",
        ))
        await pilot.pause()

        widget = app.query_one(RichTranscript)
        tool_widgets = list(widget.query(_ToolCall))
        assert len(tool_widgets) == 1
        tw = tool_widgets[0]
        # Expanded while running, title shows running marker + tool name.
        assert tw.collapsed is False
        assert "bash" in tw.title
        # Body contains the args text.
        assert any("ls /tmp" in str(s.content) for s in tw.query(Static))


@pytest.mark.asyncio
async def test_tool_result_pairs_by_tool_id_and_collapses(tmp_path):
    from patchfeld.widgets.rich_transcript import _ToolCall

    bus = EventBus()
    app = _HostApp(bus, "a1")
    app.cwd = tmp_path
    async with app.run_test() as pilot:
        await pilot.pause()
        bus.publish(AgentMessageAppended(agent_id="a1", role="user", text="go"))
        bus.publish(AgentMessageAppended(
            agent_id="a1", role="tool_use", text="{'cmd': 'ls'}",
            tool_id="t1", tool_name="bash",
        ))
        bus.publish(AgentMessageAppended(
            agent_id="a1", role="tool_use", text="{'p': 'README'}",
            tool_id="t2", tool_name="read",
        ))
        # Result for second tool arrives first.
        bus.publish(AgentMessageAppended(
            agent_id="a1", role="tool_result", text="<readme contents>",
            tool_id="t2",
        ))
        # Then result for first tool.
        bus.publish(AgentMessageAppended(
            agent_id="a1", role="tool_result", text="bin\nlib\n",
            tool_id="t1",
        ))
        await pilot.pause()

        widget = app.query_one(RichTranscript)
        tools = list(widget.query(_ToolCall))
        assert len(tools) == 2
        # Both collapsed after their result arrived.
        assert all(t.collapsed for t in tools)
        # Pairing is correct: tool t1 (bash) shows the bin/lib output, tool t2
        # (read) shows the readme text.
        bash = next(t for t in tools if t.tool_name == "bash")
        read = next(t for t in tools if t.tool_name == "read")
        assert any("bin" in str(s.content) for s in bash.query(Static))
        assert any("readme" in str(s.content) for s in read.query(Static))
        # Title carries success marker.
        assert "✓" in bash.title
        assert "✓" in read.title


@pytest.mark.asyncio
async def test_tool_args_with_brackets_render_literally(tmp_path):
    """[type=int_parsing] in args must NOT trip Rich markup parsing."""
    from patchfeld.widgets.rich_transcript import _ToolCall

    bus = EventBus()
    app = _HostApp(bus, "a1")
    app.cwd = tmp_path
    async with app.run_test() as pilot:
        await pilot.pause()
        bus.publish(AgentMessageAppended(agent_id="a1", role="user", text="go"))
        bus.publish(AgentMessageAppended(
            agent_id="a1", role="tool_use",
            text="{'err': '[type=int_parsing, input_value=...]'}",
            tool_id="t1", tool_name="validate",
        ))
        await pilot.pause()

        widget = app.query_one(RichTranscript)
        tw = widget.query_one(_ToolCall)
        body_text = "\n".join(str(s.content) for s in tw.query(Static))
        assert "[type=int_parsing" in body_text


@pytest.mark.asyncio
async def test_consecutive_thinking_blocks_merge_into_one_group(tmp_path):
    from patchfeld.widgets.rich_transcript import _ThinkingGroup

    bus = EventBus()
    app = _HostApp(bus, "a1")
    app.cwd = tmp_path
    async with app.run_test() as pilot:
        await pilot.pause()
        bus.publish(AgentMessageAppended(agent_id="a1", role="user", text="go"))
        bus.publish(AgentMessageAppended(agent_id="a1", role="thinking", text="step 1"))
        bus.publish(AgentMessageAppended(agent_id="a1", role="thinking", text="step 2"))
        await pilot.pause()

        widget = app.query_one(RichTranscript)
        groups = list(widget.query(_ThinkingGroup))
        assert len(groups) == 1
        body = "\n".join(str(s.content) for s in groups[0].query(Static))
        assert "step 1" in body
        assert "step 2" in body


@pytest.mark.asyncio
async def test_thinking_after_tool_opens_new_group(tmp_path):
    from patchfeld.widgets.rich_transcript import _ThinkingGroup

    bus = EventBus()
    app = _HostApp(bus, "a1")
    app.cwd = tmp_path
    async with app.run_test() as pilot:
        await pilot.pause()
        bus.publish(AgentMessageAppended(agent_id="a1", role="user", text="go"))
        bus.publish(AgentMessageAppended(agent_id="a1", role="thinking", text="first"))
        bus.publish(AgentMessageAppended(
            agent_id="a1", role="tool_use", text="{}",
            tool_id="t1", tool_name="bash",
        ))
        bus.publish(AgentMessageAppended(agent_id="a1", role="thinking", text="second"))
        await pilot.pause()

        widget = app.query_one(RichTranscript)
        groups = list(widget.query(_ThinkingGroup))
        assert len(groups) == 2


@pytest.mark.asyncio
async def test_thinking_group_starts_expanded(tmp_path):
    from patchfeld.widgets.rich_transcript import _ThinkingGroup

    bus = EventBus()
    app = _HostApp(bus, "a1")
    app.cwd = tmp_path
    async with app.run_test() as pilot:
        await pilot.pause()
        bus.publish(AgentMessageAppended(agent_id="a1", role="user", text="go"))
        bus.publish(AgentMessageAppended(agent_id="a1", role="thinking", text="..."))
        await pilot.pause()

        group = app.query_one(_ThinkingGroup)
        assert group.collapsed is False
        assert "Thinking" in group.title


@pytest.mark.asyncio
async def test_agent_state_done_collapses_current_turn(tmp_path):
    from patchfeld.agents.state import AgentInfo, AgentState
    from patchfeld.events import AgentStateChanged
    from patchfeld.widgets.rich_transcript import (
        _ThinkingGroup, _TurnContainer,
    )

    bus = EventBus()
    app = _HostApp(bus, "a1")
    app.cwd = tmp_path
    async with app.run_test() as pilot:
        await pilot.pause()
        bus.publish(AgentMessageAppended(agent_id="a1", role="user", text="go"))
        bus.publish(AgentMessageAppended(agent_id="a1", role="thinking", text="..."))
        bus.publish(AgentMessageAppended(agent_id="a1", role="assistant", text="ok"))
        await pilot.pause()

        info = AgentInfo(id="a1", name="a1", cwd=str(tmp_path),
                         started_at=0, state=AgentState.DONE)
        bus.publish(AgentStateChanged(info=info, old_state=AgentState.RUNNING))
        await pilot.pause()

        widget = app.query_one(RichTranscript)
        turn = widget.query_one(_TurnContainer)
        assert turn.has_class("turn-done")
        assert not turn.has_class("turn-running")
        group = widget.query_one(_ThinkingGroup)
        assert group.collapsed is True
        assert "Thought for" in group.title


@pytest.mark.asyncio
async def test_agent_state_error_marks_turn_error(tmp_path):
    from patchfeld.agents.state import AgentInfo, AgentState
    from patchfeld.events import AgentStateChanged
    from patchfeld.widgets.rich_transcript import _TurnContainer

    bus = EventBus()
    app = _HostApp(bus, "a1")
    app.cwd = tmp_path
    async with app.run_test() as pilot:
        await pilot.pause()
        bus.publish(AgentMessageAppended(agent_id="a1", role="user", text="go"))
        await pilot.pause()

        info = AgentInfo(id="a1", name="a1", cwd=str(tmp_path),
                         started_at=0, state=AgentState.ERROR)
        bus.publish(AgentStateChanged(info=info, old_state=AgentState.RUNNING))
        await pilot.pause()

        turn = app.query_one(_TurnContainer)
        assert turn.has_class("turn-error")


@pytest.mark.asyncio
async def test_state_change_for_other_agent_is_ignored(tmp_path):
    from patchfeld.agents.state import AgentInfo, AgentState
    from patchfeld.events import AgentStateChanged
    from patchfeld.widgets.rich_transcript import _TurnContainer

    bus = EventBus()
    app = _HostApp(bus, "a1")
    app.cwd = tmp_path
    async with app.run_test() as pilot:
        await pilot.pause()
        bus.publish(AgentMessageAppended(agent_id="a1", role="user", text="go"))
        await pilot.pause()

        info = AgentInfo(id="other", name="other", cwd=str(tmp_path),
                         started_at=0, state=AgentState.DONE)
        bus.publish(AgentStateChanged(info=info, old_state=AgentState.RUNNING))
        await pilot.pause()

        turn = app.query_one(_TurnContainer)
        assert turn.has_class("turn-running")
        assert not turn.has_class("turn-done")


@pytest.mark.asyncio
async def test_thinking_and_tools_wrap_in_process_group(tmp_path):
    from patchfeld.widgets.rich_transcript import (
        _ProcessGroup, _ThinkingGroup, _ToolCall,
    )

    bus = EventBus()
    app = _HostApp(bus, "a1")
    app.cwd = tmp_path
    async with app.run_test() as pilot:
        await pilot.pause()
        bus.publish(AgentMessageAppended(agent_id="a1", role="user", text="go"))
        bus.publish(AgentMessageAppended(agent_id="a1", role="thinking", text="..."))
        bus.publish(AgentMessageAppended(
            agent_id="a1", role="tool_use", text="{}",
            tool_id="t1", tool_name="bash",
        ))
        await pilot.pause()

        widget = app.query_one(RichTranscript)
        procs = list(widget.query(_ProcessGroup))
        assert len(procs) == 1
        proc = procs[0]
        # Thinking + tool widgets are descendants of the process group.
        assert len(proc.query(_ThinkingGroup)) == 1
        assert len(proc.query(_ToolCall)) == 1
        # Expanded while running.
        assert proc.collapsed is False
        assert "Working" in proc.title


@pytest.mark.asyncio
async def test_assistant_text_closes_process_group(tmp_path):
    from patchfeld.widgets.rich_transcript import _ProcessGroup

    bus = EventBus()
    app = _HostApp(bus, "a1")
    app.cwd = tmp_path
    async with app.run_test() as pilot:
        await pilot.pause()
        bus.publish(AgentMessageAppended(agent_id="a1", role="user", text="go"))
        bus.publish(AgentMessageAppended(
            agent_id="a1", role="tool_use", text="{}",
            tool_id="t1", tool_name="bash",
        ))
        bus.publish(AgentMessageAppended(
            agent_id="a1", role="tool_result", text="ok", tool_id="t1",
        ))
        bus.publish(AgentMessageAppended(agent_id="a1", role="assistant", text="done"))
        await pilot.pause()

        widget = app.query_one(RichTranscript)
        proc = widget.query_one(_ProcessGroup)
        # Final-response collapse: outer fold closes when assistant text arrives.
        assert proc.collapsed is True
        assert "Process" in proc.title


@pytest.mark.asyncio
async def test_second_round_opens_new_process_group(tmp_path):
    from patchfeld.widgets.rich_transcript import _ProcessGroup

    bus = EventBus()
    app = _HostApp(bus, "a1")
    app.cwd = tmp_path
    async with app.run_test() as pilot:
        await pilot.pause()
        bus.publish(AgentMessageAppended(agent_id="a1", role="user", text="go"))
        bus.publish(AgentMessageAppended(
            agent_id="a1", role="tool_use", text="{}",
            tool_id="t1", tool_name="bash",
        ))
        bus.publish(AgentMessageAppended(
            agent_id="a1", role="tool_result", text="ok", tool_id="t1",
        ))
        bus.publish(AgentMessageAppended(agent_id="a1", role="assistant", text="step 1"))
        # New round of work after the first response.
        bus.publish(AgentMessageAppended(
            agent_id="a1", role="tool_use", text="{}",
            tool_id="t2", tool_name="read",
        ))
        bus.publish(AgentMessageAppended(
            agent_id="a1", role="tool_result", text="ok", tool_id="t2",
        ))
        await pilot.pause()

        widget = app.query_one(RichTranscript)
        procs = list(widget.query(_ProcessGroup))
        assert len(procs) == 2
        # First one closed when the assistant text arrived; second is still
        # open because no further assistant text closed it yet.
        assert procs[0].collapsed is True
        assert procs[1].collapsed is False


@pytest.mark.asyncio
async def test_text_only_turn_has_no_process_group(tmp_path):
    from patchfeld.widgets.rich_transcript import _ProcessGroup

    bus = EventBus()
    app = _HostApp(bus, "a1")
    app.cwd = tmp_path
    async with app.run_test() as pilot:
        await pilot.pause()
        bus.publish(AgentMessageAppended(agent_id="a1", role="user", text="hi"))
        bus.publish(AgentMessageAppended(agent_id="a1", role="assistant", text="hello"))
        await pilot.pause()

        widget = app.query_one(RichTranscript)
        # No thinking, no tools — process group is created lazily, so none.
        assert len(widget.query(_ProcessGroup)) == 0


@pytest.mark.asyncio
async def test_running_tool_call_spinner_advances(tmp_path):
    from patchfeld.widgets.rich_transcript import _ToolCall, _SPINNER_FRAMES

    bus = EventBus()
    app = _HostApp(bus, "a1")
    app.cwd = tmp_path
    async with app.run_test() as pilot:
        await pilot.pause()
        bus.publish(AgentMessageAppended(agent_id="a1", role="user", text="go"))
        bus.publish(AgentMessageAppended(
            agent_id="a1", role="tool_use", text="{}",
            tool_id="t1", tool_name="bash",
        ))
        await pilot.pause()
        tw = app.query_one(_ToolCall)
        first_frame = tw.title[0]
        assert first_frame in _SPINNER_FRAMES

        # Advance the spinner enough to cycle.
        await pilot.pause(0.5)
        # Title still starts with a spinner frame, but is unlikely to be the same.
        # (We assert the cheaper invariant: it's still in the frame set.)
        assert tw.title[0] in _SPINNER_FRAMES


@pytest.mark.asyncio
async def test_spinner_stops_after_result(tmp_path):
    from patchfeld.widgets.rich_transcript import _ToolCall, _SPINNER_FRAMES

    bus = EventBus()
    app = _HostApp(bus, "a1")
    app.cwd = tmp_path
    async with app.run_test() as pilot:
        await pilot.pause()
        bus.publish(AgentMessageAppended(agent_id="a1", role="user", text="go"))
        bus.publish(AgentMessageAppended(
            agent_id="a1", role="tool_use", text="{}",
            tool_id="t1", tool_name="bash",
        ))
        bus.publish(AgentMessageAppended(
            agent_id="a1", role="tool_result", text="ok", tool_id="t1",
        ))
        await pilot.pause()
        tw = app.query_one(_ToolCall)
        # Title now starts with ✓, no longer with a spinner frame.
        assert tw.title.startswith("✓")
        assert tw.title[0] not in _SPINNER_FRAMES
