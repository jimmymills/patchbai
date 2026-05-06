from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Input

from mod_tui.events import EventBus, UserMessageToOrchestrator
from mod_tui.widgets.rich_transcript import RichTranscript


class OrchestratorChat(Vertical):
    """Manager-Claude chat panel: RichTranscript + input box."""

    AGENT_ID = "orchestrator"

    DEFAULT_CSS = """
    OrchestratorChat {
        border: round $primary;
        padding: 0 1;
    }
    OrchestratorChat > RichTranscript {
        height: 1fr;
    }
    OrchestratorChat #orch-input {
        dock: bottom;
        height: 3;
    }
    """

    def __init__(self, *, event_bus: EventBus | None = None) -> None:
        super().__init__()
        self._bus = event_bus

    def compose(self) -> ComposeResult:
        yield RichTranscript(agent_id=self.AGENT_ID, event_bus=self._bus)
        yield Input(placeholder="Message orchestrator… (enter to send)",
                    id="orch-input")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if not event.value.strip():
            return
        text = event.value
        bus = self._bus or getattr(self.app, "event_bus", None)
        event.input.value = ""
        if bus is not None:
            bus.publish(UserMessageToOrchestrator(text))
