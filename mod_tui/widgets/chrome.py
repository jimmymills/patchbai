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


class StatusBar(Horizontal):
    """Bottom bar: tokens / cost / active agents / current layout name / [E]."""

    DEFAULT_CSS = """
    StatusBar {
        height: 1;
        background: $surface-darken-1;
    }
    StatusBar Static {
        padding: 0 1;
    }
    """

    def __init__(self, *, event_bus: EventBus | None = None,
                 layout_name: str = "default") -> None:
        super().__init__()
        self._bus = event_bus
        self._layout_name = layout_name
        self._unsub = lambda: None

    def compose(self) -> ComposeResult:
        yield Static("tokens 0/0", id="sb-tokens")
        yield Static("$0.00", id="sb-cost")
        yield Static("0 agents", id="sb-agents")
        yield Static(f"layout: {self._layout_name}", id="sb-layout")
        yield Static("", id="sb-error")

    def on_mount(self) -> None:
        from mod_tui.events import StatsUpdated
        bus = self._bus or getattr(self.app, "event_bus", None)
        if bus is None:
            return
        self._unsub = bus.subscribe(StatsUpdated, self._on_stats)

    def on_unmount(self) -> None:
        self._unsub()

    def _on_stats(self, event) -> None:
        self.query_one("#sb-tokens", Static).update(
            f"tokens {event.tokens_in}/{event.tokens_out}"
        )
        self.query_one("#sb-cost", Static).update(f"${event.cost:.2f}")
        self.query_one("#sb-agents", Static).update(f"{event.active_agents} agents")

    def set_layout_name(self, name: str) -> None:
        self._layout_name = name
        self.query_one("#sb-layout", Static).update(f"layout: {name}")

    def set_error(self, msg: str | None) -> None:
        self.query_one("#sb-error", Static).update("[E]" if msg else "")
