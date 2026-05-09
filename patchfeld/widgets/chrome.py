import os
from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Input, Static

from patchfeld.events import EventBus, OrchestratorReply, UserMessageToOrchestrator
from patchfeld.orchestrator.slash_completion import SlashCompleter, SlashSuggester


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

    def __init__(
        self,
        *,
        event_bus: EventBus | None = None,
        slash_completer: SlashCompleter | None = None,
    ) -> None:
        super().__init__()
        self._bus = event_bus
        # Optional injected completer. When unset we fall back to the host
        # app's `slash_completer` attribute on first use — production wiring
        # exposes one there at app construction so newly-mounted CommandBars
        # share the same snapshot across `/cd` rebuilds.
        self._completer = slash_completer
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
        # Attach the ghost-text suggester after mount so we can fall back to
        # `app.slash_completer` when no completer was injected at construction.
        # Set every time on_mount fires (theme/cwd swap rebuilds the widget)
        # so the suggester always points at the live completer instance.
        completer = self._resolve_completer()
        if completer is not None:
            try:
                self.query_one("#cmd-input", Input).suggester = (
                    SlashSuggester(completer)
                )
            except Exception:
                pass

    def on_unmount(self) -> None:
        self._unsub_reply()

    def focus_input(self) -> None:
        self.query_one("#cmd-input", Input).focus()

    def _resolve_completer(self) -> SlashCompleter | None:
        """Late-bound completer lookup: prefer the constructor-injected one,
        fall back to whatever the host app exposes. Returning None disables
        completion entirely (Tab falls through)."""
        if self._completer is not None:
            return self._completer
        return getattr(self.app, "slash_completer", None)

    def on_key(self, event) -> None:
        """Intercept Tab / Shift+Tab to apply slash-command completion in
        place. When completion does not apply (no completer, empty input,
        text without a leading slash, mid-argument), we leave the event
        alone so Textual's default focus traversal still runs."""
        if event.key not in ("tab", "shift+tab"):
            return
        completer = self._resolve_completer()
        if completer is None:
            return
        try:
            inp = self.query_one("#cmd-input", Input)
        except Exception:
            return
        # Only intercept when the input owns focus — otherwise we'd shadow
        # Tab traversal between widgets.
        if not inp.has_focus:
            return
        direction = -1 if event.key == "shift+tab" else 1
        result = completer.cycle(
            key=inp.id or "cmd-input",
            current_text=inp.value,
            direction=direction,
        )
        if result is None:
            return  # let Tab fall through to focus_next/_previous
        inp.value = result.text
        try:
            inp.cursor_position = result.cursor
        except Exception:
            pass
        event.stop()
        event.prevent_default()

    def on_input_changed(self, event: Input.Changed) -> None:
        """Reset the completer's cycle state on any user-driven edit. The
        cycle path detects "user did not edit between Tabs" by comparing
        the input value to its own last write — that comparison stays
        correct without an explicit reset, but wiping state here also
        frees memory once the user moves on to a different prefix."""
        if event.input.id != "cmd-input":
            return
        completer = self._resolve_completer()
        if completer is None:
            return
        # Skip resets that fired as a side-effect of our own write — those
        # cases keep the cycle anchor intact so consecutive Tabs advance.
        state = completer._cycle_state.get(event.input.id or "cmd-input")
        if state is not None and state.get("last_set") == event.input.value:
            return
        completer.reset(event.input.id or "cmd-input")

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
        width: auto;
        padding: 0 1;
    }
    /* The hints Static expands to absorb all leftover horizontal space and
       right-aligns its text, parking the shortcut hint flush with the right
       edge of the bar regardless of how wide the terminal is. The id selector
       outranks the type selector above (CSS specificity), so it overrides
       `width: auto` for this one widget. */
    StatusBar #sb-hints {
        width: 1fr;
        text-align: right;
        color: $text-muted;
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
        # Always-visible hint for the two most fundamental keybindings:
        # `?` opens the help notification, `Ctrl+Q` quits. Verified against
        # PatchfeldApp.BINDINGS. New users never have to guess how to escape.
        yield Static("? help · ^Q quit", id="sb-hints")

    def on_mount(self) -> None:
        from patchfeld.events import LayoutApplied, StatsUpdated, WorkspaceCwdChanged
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

    def on_resize(self, _event) -> None:
        # Re-render so the cwd budget tracks the actual container width.
        if self._cwd_path is not None:
            self._render_cwd(self._cwd_path)

    def set_layout_name(self, name: str) -> None:
        self._layout_name = name
        self.query_one("#sb-layout", Static).update(f"layout: {name}")

    def set_error(self, msg: str | None) -> None:
        self.query_one("#sb-error", Static).update("[E]" if msg else "")
