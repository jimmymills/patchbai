from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import Input

from patchfeld.events import EventBus, UserMessageToOrchestrator
from patchfeld.orchestrator.slash_completion import SlashCompleter
from patchfeld.widgets.rich_transcript import RichTranscript


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
                "(/reset, /resume, /rename, /help, ctrl+c to interrupt)"
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

    def _resolve_completer(self) -> SlashCompleter | None:
        """Late-bound lookup of the host app's SlashCompleter. None disables
        Tab completion (Tab falls through to default focus traversal)."""
        return getattr(self.app, "slash_completer", None)

    def on_key(self, event) -> None:
        """Apply slash-command completion in place when the chat input owns
        focus, the value triggers completion (`/` prefix, no whitespace
        outside an active cycle), and there is at least one match. Falls
        through silently otherwise — Textual's default Tab traversal still
        runs."""
        if event.key not in ("tab", "shift+tab"):
            return
        completer = self._resolve_completer()
        if completer is None:
            return
        try:
            inp = self.query_one("#orch-input", Input)
        except Exception:
            return
        if not inp.has_focus:
            return
        direction = -1 if event.key == "shift+tab" else 1
        result = completer.cycle(
            key=inp.id or "orch-input",
            current_text=inp.value,
            direction=direction,
        )
        if result is None:
            return
        inp.value = result.text
        try:
            inp.cursor_position = result.cursor
        except Exception:
            pass
        event.stop()
        event.prevent_default()

    def on_input_changed(self, event: Input.Changed) -> None:
        """Drop cycle state on edits the user (not us) made — keeps the
        completer's per-widget state from leaking across unrelated prefixes.
        Detection mirrors `CommandBar.on_input_changed`."""
        if event.input.id != "orch-input":
            return
        completer = self._resolve_completer()
        if completer is None:
            return
        state = completer._cycle_state.get(event.input.id or "orch-input")
        if state is not None and state.get("last_set") == event.input.value:
            return
        completer.reset(event.input.id or "orch-input")

    async def action_interrupt(self) -> None:
        orch = getattr(self.app, "orchestrator", None)
        if orch is None:
            return
        await orch.interrupt()
