import pytest
from textual.app import App
from textual.widgets import Input

from patchbai.events import DirectMessageToAgent, EventBus
from patchbai.widgets.agent_transcript import AgentTranscript


class _HostApp(App):
    def __init__(self, bus: EventBus, agent_id: str) -> None:
        super().__init__()
        self.event_bus = bus
        self._agent_id = agent_id

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
