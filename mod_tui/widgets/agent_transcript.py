from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Input, Static

from mod_tui.events import (
    AgentMessageAppended,
    DirectMessageToAgent,
    EventBus,
)
from mod_tui.persistence.transcript_store import AgentTranscript as TranscriptStore


class AgentTranscript(Vertical):
    """Scrollable, live-updating transcript view for one agent, with input."""

    DEFAULT_CSS = """
    AgentTranscript {
        border: round $surface-lighten-2;
        padding: 0 1;
    }
    AgentTranscript #transcript-scroll {
        height: 1fr;
    }
    AgentTranscript #transcript-input {
        dock: bottom;
        height: 3;
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
        yield VerticalScroll(id="transcript-scroll")
        yield Input(placeholder=f"Message {self._agent_id}…", id="transcript-input")

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

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return
        bus = self._bus or getattr(self.app, "event_bus", None)
        if bus is not None:
            bus.publish(DirectMessageToAgent(agent_id=self._agent_id, text=text))
        event.input.value = ""

    def _on_appended(self, event: AgentMessageAppended) -> None:
        if event.agent_id != self._agent_id:
            return
        self._append_line(event.role, event.text)

    def _append_line(self, role: str, text: str) -> None:
        # Build the line via Rich Text so dict reprs / [type=...] markup-like
        # text in tool args never go through the markup parser.
        from rich.text import Text
        scroll = self.query_one("#transcript-scroll", VerticalScroll)
        line = Text()
        line.append(f"{role}: ", style="bold")
        line.append(text)
        widget = Static(line, classes=f"role-{role}")
        self._lines.append(f"[{role}] {text}")
        scroll.mount(widget)
        scroll.scroll_end(animate=False)

    def rendered_text(self) -> str:
        """Test helper — returns concatenated rendered text."""
        return "\n".join(self._lines)
