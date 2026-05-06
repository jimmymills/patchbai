import pytest
from textual.app import App
from textual.widgets import Static

from mod_tui.events import AgentMessageAppended, EventBus
from mod_tui.persistence.transcript_store import AgentTranscript as Store, TranscriptEntry
from mod_tui.widgets.rich_transcript import RichTranscript


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
    from mod_tui.widgets.rich_transcript import _TurnContainer

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
    from mod_tui.widgets.rich_transcript import _TurnContainer

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
