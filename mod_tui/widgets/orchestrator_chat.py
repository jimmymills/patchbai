from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Input, Static

from mod_tui.events import EventBus, OrchestratorReply, UserMessageToOrchestrator


class OrchestratorChat(Vertical):
    """Manager-Claude chat panel: scrolling message list + input box."""

    DEFAULT_CSS = """
    OrchestratorChat {
        border: round $primary;
        padding: 0 1;
    }
    OrchestratorChat #orch-messages {
        height: 1fr;
    }
    OrchestratorChat #orch-input {
        dock: bottom;
        height: 3;
    }
    OrchestratorChat .msg-user {
        color: $accent;
    }
    OrchestratorChat .msg-orch {
        color: $text;
    }
    """

    def __init__(self, *, event_bus: EventBus | None = None,
                 history: list[tuple[str, str]] | None = None) -> None:
        """history: optional list of (role, text) preloaded messages."""
        super().__init__()
        self._bus = event_bus
        self._history = history or []
        self._unsub = lambda: None

    def compose(self) -> ComposeResult:
        yield VerticalScroll(id="orch-messages")
        yield Input(placeholder="Message orchestrator… (enter to send)",
                    id="orch-input")

    def on_mount(self) -> None:
        bus = self._bus or getattr(self.app, "event_bus", None)
        if bus is None:
            return
        # Prefer fresh on-disk transcript over the app's startup snapshot —
        # the snapshot doesn't include messages from this session, so a
        # set_layout-driven remount would otherwise lose all live history.
        if self._history:
            history = list(self._history)
        else:
            cwd = getattr(self.app, "cwd", None)
            if cwd is not None:
                from mod_tui.persistence.transcript_store import (
                    OrchestratorTranscript,
                )
                entries = OrchestratorTranscript(cwd=cwd).read_all()
                history = [(e.role, e.text) for e in entries]
            else:
                history = list(getattr(self.app, "orchestrator_history", []))
        for role, text in history:
            self._append_line(role, text)
        self._unsub = bus.subscribe(
            OrchestratorReply, lambda e: self._append_line("orch", e.text)
        )

    def on_unmount(self) -> None:
        self._unsub()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if not event.value.strip():
            return
        text = event.value
        bus = self._bus or getattr(self.app, "event_bus", None)
        self._append_line("user", text)
        event.input.value = ""
        if bus is not None:
            bus.publish(UserMessageToOrchestrator(text))

    def _append_line(self, role: str, text: str) -> None:
        msgs = self.query_one("#orch-messages", VerticalScroll)
        prefix = "you" if role == "user" else "claude"
        cls = "msg-user" if role == "user" else "msg-orch"
        msgs.mount(Static(f"[{cls}]{prefix}:[/{cls}] {text}"))
        msgs.scroll_end(animate=False)
