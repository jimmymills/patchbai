from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Input, Static

from mod_tui.events import EventBus, UserMessageToOrchestrator


class CommandBar(Horizontal):
    """Top bar — `/` focuses; submitting sends to the orchestrator."""

    DEFAULT_CSS = """
    CommandBar {
        height: 1;
        background: $surface-darken-1;
    }
    CommandBar Input {
        border: none;
        padding: 0;
        background: $surface-darken-1;
    }
    CommandBar Static {
        width: 7;
        color: $text-muted;
    }
    """

    def __init__(self, *, event_bus: EventBus | None = None) -> None:
        super().__init__()
        self._bus = event_bus

    def compose(self) -> ComposeResult:
        yield Static("mt :> ")
        yield Input(placeholder="message orchestrator", id="cmd-input")

    def focus_input(self) -> None:
        self.query_one("#cmd-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if not event.value.strip():
            return
        bus = self._bus or getattr(self.app, "event_bus", None)
        if bus is not None:
            bus.publish(UserMessageToOrchestrator(event.value))
        event.input.value = ""
