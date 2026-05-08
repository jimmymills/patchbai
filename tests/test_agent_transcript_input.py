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


@pytest.mark.asyncio
async def test_ctrl_c_interrupts_agent_from_transcript_input():
    """ctrl+c on the agent transcript input must call manager.interrupt(agent_id),
    mirroring the orchestrator chat's ctrl+c behavior. Without this binding,
    Textual's default driver handling consumes ctrl+c and quits the app."""

    class _SpyManager:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def interrupt(self, agent_id: str) -> None:
            self.calls.append(agent_id)

    bus = EventBus()
    manager = _SpyManager()
    app = _HostApp(bus, "a1", manager=manager)
    # No app.cwd needed — this test doesn't exercise transcript persistence.
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
