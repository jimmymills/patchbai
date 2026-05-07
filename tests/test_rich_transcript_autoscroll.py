import pytest
from textual.app import App
from textual.containers import VerticalScroll

from mod_tui.events import AgentMessageAppended, EventBus
from mod_tui.widgets.rich_transcript import RichTranscript


class _HostApp(App):
    """Host with a constrained-height RichTranscript so a few entries are
    enough to overflow the viewport."""

    CSS = """
    RichTranscript { height: 8; }
    """

    def __init__(self, bus: EventBus, agent_id: str) -> None:
        super().__init__()
        self.event_bus = bus
        self._agent_id = agent_id

    def compose(self):
        yield RichTranscript(agent_id=self._agent_id, event_bus=self.event_bus)


def _seed_overflow(bus: EventBus, agent_id: str, n_assistants: int = 8) -> None:
    """One user turn, then many assistant lines so total content exceeds the
    constrained 8-row viewport in the test app."""
    bus.publish(AgentMessageAppended(agent_id=agent_id, role="user", text="hello"))
    for i in range(n_assistants):
        bus.publish(AgentMessageAppended(
            agent_id=agent_id, role="assistant", text=f"line {i}",
        ))


@pytest.mark.asyncio
async def test_autoscroll_pins_to_bottom_when_already_at_end(tmp_path):
    bus = EventBus()
    app = _HostApp(bus, "a1")
    app.cwd = tmp_path
    async with app.run_test(size=(40, 12)) as pilot:
        await pilot.pause()
        _seed_overflow(bus, "a1")
        await pilot.pause()
        widget = app.query_one(RichTranscript)
        scroll = widget.query_one(VerticalScroll)
        assert scroll.is_vertical_scroll_end, (
            "expected viewport pinned to bottom after live appends"
        )


@pytest.mark.asyncio
async def test_autoscroll_does_not_yank_user_who_scrolled_up(tmp_path):
    bus = EventBus()
    app = _HostApp(bus, "a1")
    app.cwd = tmp_path
    async with app.run_test(size=(40, 12)) as pilot:
        await pilot.pause()
        _seed_overflow(bus, "a1")
        await pilot.pause()
        widget = app.query_one(RichTranscript)
        scroll = widget.query_one(VerticalScroll)
        assert scroll.is_vertical_scroll_end

        # User scrolls up to read history.
        scroll.scroll_home(animate=False)
        await pilot.pause()
        assert not scroll.is_vertical_scroll_end
        scroll_y_before = scroll.scroll_y

        # New message arrives — must NOT snap us back to the bottom.
        bus.publish(AgentMessageAppended(
            agent_id="a1", role="assistant", text="late arrival",
        ))
        await pilot.pause()
        assert not scroll.is_vertical_scroll_end
        assert scroll.scroll_y == scroll_y_before
