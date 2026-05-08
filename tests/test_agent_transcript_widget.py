import pytest
from textual.app import App

from patchfeld.events import AgentMessageAppended, EventBus
from patchfeld.persistence.transcript_store import AgentTranscript as Store, TranscriptEntry
from patchfeld.widgets.agent_transcript import AgentTranscript


class _HostApp(App):
    def __init__(self, bus: EventBus, agent_id: str) -> None:
        super().__init__()
        self.event_bus = bus
        self._agent_id = agent_id

    def compose(self):
        yield AgentTranscript(agent_id=self._agent_id, event_bus=self.event_bus)


@pytest.mark.asyncio
async def test_agent_transcript_renders_initial_history(tmp_path):
    store = Store(cwd=tmp_path, agent_id="a1")
    store.append(TranscriptEntry(role="user", text="hello"))
    store.append(TranscriptEntry(role="assistant", text="hi"))

    bus = EventBus()
    app = _HostApp(bus, "a1")
    app.cwd = tmp_path
    async with app.run_test() as pilot:
        await pilot.pause()
        widget = app.query_one(AgentTranscript)
        rendered = widget.rendered_text()
        assert "hello" in rendered
        assert "hi" in rendered


@pytest.mark.asyncio
async def test_agent_transcript_appends_live_messages(tmp_path):
    bus = EventBus()
    app = _HostApp(bus, "a1")
    app.cwd = tmp_path
    async with app.run_test() as pilot:
        await pilot.pause()
        bus.publish(AgentMessageAppended(agent_id="a1", role="assistant", text="live!"))
        await pilot.pause()
        widget = app.query_one(AgentTranscript)
        assert "live!" in widget.rendered_text()


@pytest.mark.asyncio
async def test_agent_transcript_ignores_other_agents(tmp_path):
    bus = EventBus()
    app = _HostApp(bus, "a1")
    app.cwd = tmp_path
    async with app.run_test() as pilot:
        await pilot.pause()
        bus.publish(AgentMessageAppended(agent_id="b2", role="assistant", text="leak"))
        await pilot.pause()
        widget = app.query_one(AgentTranscript)
        assert "leak" not in widget.rendered_text()


@pytest.mark.asyncio
async def test_agent_transcript_uses_rich_transcript(tmp_path):
    from patchfeld.widgets.agent_transcript import AgentTranscript as Widget
    from patchfeld.widgets.rich_transcript import RichTranscript

    bus = EventBus()
    app = _HostApp(bus, "a1")
    app.cwd = tmp_path
    async with app.run_test() as pilot:
        await pilot.pause()
        widget = app.query_one(Widget)
        rt = widget.query_one(RichTranscript)
        assert rt.agent_id == "a1"
