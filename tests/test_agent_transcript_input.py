import pytest
from textual.app import App
from textual.widgets import Input

from patchfeld.events import DirectMessageToAgent, EventBus
from patchfeld.widgets.agent_transcript import AgentTranscript


class _HostApp(App):
    def __init__(self, bus: EventBus, agent_id: str, manager: object | None = None) -> None:
        super().__init__()
        self.event_bus = bus
        self._agent_id = agent_id
        if manager is not None:
            self.manager = manager  # type: ignore[attr-defined]

    def compose(self):
        yield AgentTranscript(agent_id=self._agent_id, event_bus=self.event_bus)


@pytest.mark.asyncio
async def test_typing_into_input_publishes_direct_message_event(tmp_path):
    bus = EventBus()
    received: list[DirectMessageToAgent] = []
    bus.subscribe(DirectMessageToAgent, received.append)

    app = _HostApp(bus, "a1")
    app.cwd = tmp_path
    async with app.run_test() as pilot:
        await pilot.pause()
        widget = app.query_one(AgentTranscript)
        input_box = widget.query_one(Input)
        input_box.value = "hi from user"
        # Simulate enter — try action_submit first; fall back to posting
        # Input.Submitted directly if action_submit doesn't exist.
        if hasattr(input_box, "action_submit"):
            await input_box.action_submit()
        else:
            input_box.post_message(Input.Submitted(input=input_box, value="hi from user"))
        await pilot.pause()

    assert received == [DirectMessageToAgent(agent_id="a1", text="hi from user")]


class _SpyManager:
    """Minimal AgentManager stand-in: tracks interrupt calls and exposes
    `get_session()` so the widget can guard against stale agent_ids."""

    def __init__(self, *, live_ids: tuple[str, ...] = ()) -> None:
        self.calls: list[str] = []
        self._live = set(live_ids)

    def get_session(self, agent_id: str):
        return object() if agent_id in self._live else None

    async def interrupt(self, agent_id: str) -> None:
        self.calls.append(agent_id)


@pytest.mark.asyncio
async def test_ctrl_c_interrupts_agent_from_transcript_input():
    """ctrl+c on the agent transcript input must call manager.interrupt(agent_id),
    mirroring the orchestrator chat's ctrl+c behavior. Without this binding,
    Textual's default driver handling consumes ctrl+c and quits the app."""

    bus = EventBus()
    manager = _SpyManager(live_ids=("a1",))
    app = _HostApp(bus, "a1", manager=manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        widget = app.query_one(AgentTranscript)
        input_box = widget.query_one(Input)
        input_box.focus()
        await pilot.pause()
        await pilot.press("ctrl+c")
        await pilot.pause()

        assert manager.calls == ["a1"]
        # Input still focused — binding didn't blur or quit the app.
        assert input_box.has_focus
        # App not exiting — default ctrl+c quit was suppressed.
        assert app._exit is False


@pytest.mark.asyncio
async def test_ctrl_c_with_stale_agent_id_does_not_call_interrupt():
    """When the panel's agent_id no longer matches a live session (e.g.
    the panel was opened from a stale agents.json entry), ctrl+c must
    NOT call manager.interrupt — the call would silently no-op and the
    user would think the binding is broken. The widget should detect
    the stale id via manager.get_session() and surface a warning toast
    instead."""

    bus = EventBus()
    # Manager with NO live sessions for "ghost".
    manager = _SpyManager(live_ids=())
    app = _HostApp(bus, "ghost", manager=manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        widget = app.query_one(AgentTranscript)
        input_box = widget.query_one(Input)
        input_box.focus()
        await pilot.pause()
        await pilot.press("ctrl+c")
        await pilot.pause()

        assert manager.calls == []  # didn't bother calling interrupt
        # Binding still consumed the key — app not exiting.
        assert app._exit is False
        assert input_box.has_focus
