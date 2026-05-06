from pathlib import Path

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Static

from mod_tui.events import AgentMessageAppended, EventBus
from mod_tui.persistence.transcript_store import AgentTranscript as TranscriptStore


class AgentTranscript(VerticalScroll):
    """Scrollable, live-updating transcript view for one agent."""

    DEFAULT_CSS = """
    AgentTranscript {
        border: round $surface-lighten-2;
        padding: 0 1;
    }
    AgentTranscript .role-user { color: $accent; }
    AgentTranscript .role-assistant { color: $text; }
    AgentTranscript .role-tool_use { color: $warning; }
    AgentTranscript .role-tool_result { color: $secondary; }
    AgentTranscript .role-thinking { color: $text-muted; }
    """

    def __init__(
        self,
        *,
        agent_id: str,
        event_bus: EventBus | None = None,
    ) -> None:
        super().__init__()
        self._agent_id = agent_id
        self._bus = event_bus
        self._unsub = lambda: None
        self._lines: list[str] = []

    def compose(self) -> ComposeResult:
        # Empty body; lines are mounted as Static children in on_mount and
        # _append_line.
        return iter(())

    def on_mount(self) -> None:
        bus = self._bus or getattr(self.app, "event_bus", None)
        cwd: Path | None = getattr(self.app, "cwd", None)
        if cwd is not None:
            store = TranscriptStore(cwd=cwd, agent_id=self._agent_id)
            for entry in store.read_all():
                self._append_line(entry.role, entry.text)
        if bus is not None:
            self._unsub = bus.subscribe(AgentMessageAppended, self._on_appended)

    def on_unmount(self) -> None:
        self._unsub()

    def _on_appended(self, event: AgentMessageAppended) -> None:
        if event.agent_id != self._agent_id:
            return
        self._append_line(event.role, event.text)

    def _append_line(self, role: str, text: str) -> None:
        widget = Static(f"[role-{role}]{role}:[/role-{role}] {text}", classes=f"role-{role}")
        self._lines.append(f"[{role}] {text}")
        self.mount(widget)
        self.scroll_end(animate=False)

    def rendered_text(self) -> str:
        """Test helper — returns concatenated rendered text."""
        return "\n".join(self._lines)
