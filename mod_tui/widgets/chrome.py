from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Input, Static

from mod_tui.events import EventBus, UserMessageToOrchestrator


class CommandBar(Horizontal):
    """Top bar — `/` focuses; submitting sends to the orchestrator."""

    DEFAULT_CSS = """
    CommandBar {
        height: 3;
    }
    CommandBar Static#cmd-prompt {
        width: 7;
        content-align: left middle;
        color: $text-muted;
    }
    """

    def __init__(self, *, event_bus: EventBus | None = None) -> None:
        super().__init__()
        self._bus = event_bus

    def compose(self) -> ComposeResult:
        yield Static("mt :> ", id="cmd-prompt")
        # Plain Textual Input with no styling overrides — default 3-row
        # height, default colors, default focus behavior. Earlier attempts
        # to compress this to 1-row (custom CSS, -textual-compact) clashed
        # with Textual's color/cursor internals and produced invisible text.
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
        self._unsub_layout = lambda: None

    def compose(self) -> ComposeResult:
        yield Static("tokens 0/0", id="sb-tokens")
        yield Static("$0.00", id="sb-cost")
        yield Static("0 agents", id="sb-agents")
        yield Static(f"layout: {self._layout_name}", id="sb-layout")
        yield Static("", id="sb-error")

    def on_mount(self) -> None:
        from mod_tui.events import LayoutApplied, StatsUpdated
        bus = self._bus or getattr(self.app, "event_bus", None)
        if bus is None:
            return
        self._unsub = bus.subscribe(StatsUpdated, self._on_stats)
        self._unsub_layout = bus.subscribe(LayoutApplied, self._on_layout_applied)

    def on_unmount(self) -> None:
        self._unsub()
        self._unsub_layout()

    def _on_stats(self, event) -> None:
        self.query_one("#sb-tokens", Static).update(
            f"tokens {event.tokens_in}/{event.tokens_out}"
        )
        self.query_one("#sb-cost", Static).update(f"${event.cost:.2f}")
        self.query_one("#sb-agents", Static).update(f"{event.active_agents} agents")

    def _on_layout_applied(self, event) -> None:
        name = event.layout_name or "default"
        self.set_layout_name(name)

    def set_layout_name(self, name: str) -> None:
        self._layout_name = name
        self.query_one("#sb-layout", Static).update(f"layout: {name}")

    def set_error(self, msg: str | None) -> None:
        self.query_one("#sb-error", Static).update("[E]" if msg else "")
