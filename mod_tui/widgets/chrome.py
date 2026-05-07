import os
from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Input, Static

from mod_tui.events import EventBus, OrchestratorReply, UserMessageToOrchestrator


def _format_cwd(path: Path, *, available_width: int) -> str:
    """Render `path` for the StatusBar, abbreviating under $HOME and
    left-truncating with '…/' to fit `available_width` characters.

    Pure: no I/O, no widget access. Drives `_on_cwd_changed` and
    `on_resize` in StatusBar.

    Uses ``os.path.abspath`` rather than ``Path.resolve`` so the
    formatter does not follow symlinks — the user sees the path they
    typed (e.g. ``/var/log/foo``) rather than the kernel-canonical form
    (``/private/var/log/foo`` on macOS). Path canonicalisation belongs
    in the validation step inside ``App.change_cwd``, not in the footer.
    """
    try:
        home = Path.home()
        abs_p = Path(os.path.abspath(path))
        try:
            rel = abs_p.relative_to(home)
            display = "~" + ("/" + str(rel) if str(rel) != "." else "")
        except ValueError:
            display = str(abs_p)
    except Exception:
        display = str(path)

    if available_width <= 0 or len(display) <= available_width:
        return display
    # Try left-truncation that ends at a segment boundary.
    parts = display.split("/")
    # Keep peeling leading segments until "…/" + tail fits.
    for keep in range(len(parts) - 1, 0, -1):
        candidate = "…/" + "/".join(parts[-keep:])
        if len(candidate) <= available_width:
            return candidate
    # Budget too tight even for "…/leaf" — return bare basename.
    return parts[-1]


class CommandBar(Horizontal):
    """Top bar — `/` focuses; submitting sends to the orchestrator."""

    DEFAULT_CSS = """
    CommandBar {
        height: 3;
    }
    """

    def __init__(self, *, event_bus: EventBus | None = None) -> None:
        super().__init__()
        self._bus = event_bus
        # True between a command-bar submit and the next OrchestratorReply.
        # Gates the toast so replies from other input surfaces (the
        # orchestrator chat panel) don't pop a toast as well.
        self._awaiting_reply = False
        self._unsub_reply = lambda: None

    def compose(self) -> ComposeResult:
        # Plain Textual Input with no styling overrides — default 3-row
        # height, default colors, default focus behavior. Earlier attempts
        # to compress this to 1-row (custom CSS, -textual-compact) clashed
        # with Textual's color/cursor internals and produced invisible text.
        yield Input(placeholder="message orchestrator", id="cmd-input")

    def on_mount(self) -> None:
        bus = self._bus or getattr(self.app, "event_bus", None)
        if bus is not None:
            self._unsub_reply = bus.subscribe(OrchestratorReply, self._on_reply)

    def on_unmount(self) -> None:
        self._unsub_reply()

    def focus_input(self) -> None:
        self.query_one("#cmd-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if not event.value.strip():
            return
        bus = self._bus or getattr(self.app, "event_bus", None)
        if bus is not None:
            self._awaiting_reply = True
            bus.publish(UserMessageToOrchestrator(event.value))
        event.input.value = ""

    def _on_reply(self, event: OrchestratorReply) -> None:
        if not self._awaiting_reply:
            return
        self._awaiting_reply = False
        text = (event.text or "").strip()
        if not text:
            return
        try:
            self.app.notify(text, title="orchestrator")
        except Exception:
            pass


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
        self._unsub_cwd = lambda: None
        self._cwd_path: Path | None = None

    def compose(self) -> ComposeResult:
        yield Static("tokens 0/0", id="sb-tokens")
        yield Static("$0.00", id="sb-cost")
        yield Static("0 agents", id="sb-agents")
        yield Static(f"layout: {self._layout_name}", id="sb-layout")
        yield Static("", id="sb-cwd")
        yield Static("", id="sb-error")

    def on_mount(self) -> None:
        from mod_tui.events import LayoutApplied, StatsUpdated, WorkspaceCwdChanged
        bus = self._bus or getattr(self.app, "event_bus", None)
        # Initial cwd render — read app.cwd directly so we display correctly
        # even if the WorkspaceCwdChanged event was published before this
        # widget mounted.
        try:
            cwd = getattr(self.app, "cwd", None)
            if cwd is not None:
                self._render_cwd(Path(cwd))
        except Exception:
            pass
        if bus is None:
            return
        self._unsub = bus.subscribe(StatsUpdated, self._on_stats)
        self._unsub_layout = bus.subscribe(LayoutApplied, self._on_layout_applied)
        self._unsub_cwd = bus.subscribe(WorkspaceCwdChanged, self._on_cwd_changed)

    def on_unmount(self) -> None:
        self._unsub()
        self._unsub_layout()
        self._unsub_cwd()

    def _on_stats(self, event) -> None:
        self.query_one("#sb-tokens", Static).update(
            f"tokens {event.tokens_in}/{event.tokens_out}"
        )
        self.query_one("#sb-cost", Static).update(f"${event.cost:.2f}")
        self.query_one("#sb-agents", Static).update(f"{event.active_agents} agents")

    def _on_layout_applied(self, event) -> None:
        name = event.layout_name or "default"
        self.set_layout_name(name)

    def _render_cwd(self, path: Path) -> None:
        self._cwd_path = path
        widget = self.query_one("#sb-cwd", Static)
        # Allocate roughly half the bar width to cwd, capped at 40 chars.
        try:
            container_width = max(self.size.width, 0)
        except Exception:
            container_width = 0
        budget = max(0, min(40, container_width // 2 if container_width else 40))
        widget.update(f"cwd: {_format_cwd(path, available_width=budget)}")

    def _on_cwd_changed(self, event) -> None:
        self._render_cwd(Path(event.cwd))

    def set_layout_name(self, name: str) -> None:
        self._layout_name = name
        self.query_one("#sb-layout", Static).update(f"layout: {name}")

    def set_error(self, msg: str | None) -> None:
        self.query_one("#sb-error", Static).update("[E]" if msg else "")
