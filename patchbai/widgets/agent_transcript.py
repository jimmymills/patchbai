from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Input

from patchbai.events import DirectMessageToAgent, EventBus
from patchbai.widgets.rich_transcript import RichTranscript


class AgentTranscript(Vertical):
    """Per-child-agent transcript panel: RichTranscript + input box."""

    DEFAULT_CSS = """
    AgentTranscript {
        border: round $surface-lighten-2;
        padding: 0 1;
    }
    AgentTranscript > RichTranscript {
        height: 1fr;
    }
    AgentTranscript #transcript-input {
        dock: bottom;
        height: 3;
    }
    """

    def __init__(
        self,
        *,
        agent_id: str,
        event_bus: EventBus | None = None,
    ) -> None:
        super().__init__()
        self._agent_id = agent_id
        self._bus = event_bus

    def compose(self) -> ComposeResult:
        yield RichTranscript(agent_id=self._agent_id, event_bus=self._bus)
        yield Input(placeholder=f"Message {self._agent_id}…", id="transcript-input")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return
        bus = self._bus or getattr(self.app, "event_bus", None)
        if bus is not None:
            bus.publish(DirectMessageToAgent(agent_id=self._agent_id, text=text))
        event.input.value = ""

    def rendered_text(self) -> str:
        """Test helper — delegates to the inner RichTranscript."""
        return self.query_one(RichTranscript).rendered_text()

    @classmethod
    def default_border_title(cls, props: dict) -> str:
        agent_id = props.get("agent_id")
        if agent_id:
            return f"Agent: {agent_id}"
        return "Agent"
