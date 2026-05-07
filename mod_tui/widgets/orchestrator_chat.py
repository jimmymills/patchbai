from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import Input

from mod_tui.events import EventBus, UserMessageToOrchestrator
from mod_tui.widgets.rich_transcript import RichTranscript


class OrchestratorChat(Vertical):
    """Manager-Claude chat panel: RichTranscript + input box."""

    AGENT_ID = "orchestrator"
    DEFAULT_BORDER_TITLE = "Orchestrator"

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

    # priority=True so the binding fires even when the Input child has
    # focus — without it, ctrl+c is consumed by Textual's default driver
    # handling (which would quit the app).
    BINDINGS = [
        Binding("ctrl+c", "interrupt", "interrupt orchestrator", priority=True),
    ]

    def __init__(self, *, event_bus: EventBus | None = None) -> None:
        super().__init__()
        self._bus = event_bus

    def compose(self) -> ComposeResult:
        path = None
        try:
            orch = getattr(self.app, "orchestrator", None)
            if orch is not None:
                path = orch.active_transcript_path
        except Exception:
            path = None
        yield RichTranscript(
            agent_id=self.AGENT_ID, event_bus=self._bus, transcript_path=path,
        )
        yield Input(
            placeholder=(
                "Message orchestrator… "
                "(/reset, /resume, /rename, ctrl+c to interrupt)"
            ),
            id="orch-input",
        )

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if not event.value.strip():
            return
        text = event.value
        bus = self._bus or getattr(self.app, "event_bus", None)
        event.input.value = ""
        if bus is not None:
            bus.publish(UserMessageToOrchestrator(text))

    async def action_interrupt(self) -> None:
        orch = getattr(self.app, "orchestrator", None)
        if orch is None:
            return
        await orch.interrupt()
