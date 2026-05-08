from textual.binding import Binding
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import Footer

from patchfeld.events import EventBus
from patchfeld.widgets.agent_transcript import AgentTranscript


class TranscriptScreen(ModalScreen[None]):
    """Modal overlay showing one agent's transcript. Esc to dismiss."""

    DEFAULT_CSS = """
    TranscriptScreen {
        align: center middle;
    }
    TranscriptScreen > Container {
        width: 80%;
        height: 80%;
        border: thick $primary;
        background: $surface;
        padding: 1 2;
    }
    """

    BINDINGS = [Binding("escape", "dismiss", "close")]

    def __init__(self, agent_id: str, event_bus: EventBus | None = None) -> None:
        super().__init__()
        self._agent_id = agent_id
        self._bus = event_bus

    def compose(self):
        with Container():
            yield AgentTranscript(agent_id=self._agent_id, event_bus=self._bus)
            yield Footer()

    def action_dismiss(self) -> None:
        self.dismiss(None)
