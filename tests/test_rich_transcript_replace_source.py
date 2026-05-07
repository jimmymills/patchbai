from pathlib import Path

import pytest
from textual.app import App

from mod_tui.events import (
    AgentMessageAppended,
    EventBus,
    OrchestratorSessionSwitched,
)
from mod_tui.persistence.transcript_store import (
    AgentTranscript as Store,
    TranscriptEntry,
)
from mod_tui.widgets.rich_transcript import RichTranscript


class _HostApp(App):
    def __init__(self, bus: EventBus, agent_id: str, path: Path | None) -> None:
        super().__init__()
        self.event_bus = bus
        self._agent_id = agent_id
        self._path = path

    def compose(self):
        yield RichTranscript(
            agent_id=self._agent_id,
            event_bus=self.event_bus,
            transcript_path=self._path,
        )


@pytest.mark.asyncio
async def test_path_override_used_for_replay(tmp_path):
    custom = tmp_path / "custom.jsonl"
    Store(cwd=tmp_path, agent_id="ignored", path=custom).append(
        TranscriptEntry(role="user", text="from custom path")
    )
    Store(cwd=tmp_path, agent_id="ignored", path=custom).append(
        TranscriptEntry(role="assistant", text="hi from custom")
    )

    bus = EventBus()
    app = _HostApp(bus, "orchestrator", custom)
    app.cwd = tmp_path
    async with app.run_test() as pilot:
        await pilot.pause()
        widget = app.query_one(RichTranscript)
        text = widget.rendered_text()
        assert "from custom path" in text
        assert "hi from custom" in text


@pytest.mark.asyncio
async def test_replace_source_clears_and_replays(tmp_path):
    path_a = tmp_path / "a.jsonl"
    path_b = tmp_path / "b.jsonl"
    Store(cwd=tmp_path, agent_id="ignored", path=path_a).append(
        TranscriptEntry(role="user", text="A1"))
    Store(cwd=tmp_path, agent_id="ignored", path=path_b).append(
        TranscriptEntry(role="user", text="B1"))
    Store(cwd=tmp_path, agent_id="ignored", path=path_b).append(
        TranscriptEntry(role="assistant", text="B2"))

    bus = EventBus()
    app = _HostApp(bus, "orchestrator", path_a)
    app.cwd = tmp_path
    async with app.run_test() as pilot:
        await pilot.pause()
        widget = app.query_one(RichTranscript)
        assert "A1" in widget.rendered_text()

        bus.publish(OrchestratorSessionSwitched(
            session_id="ignored", transcript_path=str(path_b),
        ))
        await pilot.pause()

        text = widget.rendered_text()
        assert "B1" in text
        assert "B2" in text
        assert "A1" not in text


@pytest.mark.asyncio
async def test_live_messages_for_agent_id_still_render_after_replace(tmp_path):
    path_a = tmp_path / "a.jsonl"
    path_b = tmp_path / "b.jsonl"
    Store(cwd=tmp_path, agent_id="ignored", path=path_a).append(
        TranscriptEntry(role="user", text="A1"))
    path_b.write_text("", encoding="utf-8")

    bus = EventBus()
    app = _HostApp(bus, "orchestrator", path_a)
    app.cwd = tmp_path
    async with app.run_test() as pilot:
        await pilot.pause()
        bus.publish(OrchestratorSessionSwitched(
            session_id="ignored", transcript_path=str(path_b),
        ))
        await pilot.pause()
        bus.publish(AgentMessageAppended(
            agent_id="orchestrator", role="user", text="live-after-swap"))
        await pilot.pause()
        widget = app.query_one(RichTranscript)
        assert "live-after-swap" in widget.rendered_text()
