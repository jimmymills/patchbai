from __future__ import annotations

from pathlib import Path

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Static

from mod_tui.events import AgentMessageAppended, EventBus
from mod_tui.persistence.transcript_store import (
    AgentTranscript as TranscriptStore,
    TranscriptEntry,
)


class RichTranscript(Vertical):
    """Scrollable, live-updating transcript with per-turn grouping.

    Subscribes to AgentMessageAppended (filtered by agent_id) and
    AgentStateChanged (Task 9) to render turns containing thinking groups,
    tool-call foldables, and final response text.
    """

    DEFAULT_CSS = """
    RichTranscript {
        height: 1fr;
    }
    RichTranscript > VerticalScroll {
        height: 1fr;
    }
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
        self._unsub_msg = lambda: None

    def compose(self) -> ComposeResult:
        yield VerticalScroll()

    def on_mount(self) -> None:
        # Replay on-disk history first so live events append after it.
        cwd: Path | None = getattr(self.app, "cwd", None)
        if cwd is not None:
            store = TranscriptStore(cwd=cwd, agent_id=self._agent_id)
            for entry in store.read_all():
                self._render_entry(entry)
        bus = self._bus or getattr(self.app, "event_bus", None)
        if bus is not None:
            self._unsub_msg = bus.subscribe(AgentMessageAppended, self._on_appended)

    def on_unmount(self) -> None:
        self._unsub_msg()

    def _on_appended(self, event: AgentMessageAppended) -> None:
        if event.agent_id != self._agent_id:
            return
        # Skeleton: render every event as a flat line. Tasks 6–9 replace this
        # with turn-grouped foldables.
        self._render_entry(TranscriptEntry(
            role=event.role, text=event.text,
            tool_id=event.tool_id, tool_name=event.tool_name,
        ))

    def _render_entry(self, entry: TranscriptEntry) -> None:
        scroll = self.query_one(VerticalScroll)
        line = Text()
        line.append(f"{entry.role}: ", style="bold")
        line.append(entry.text)
        scroll.mount(Static(line))
        scroll.scroll_end(animate=False)

    # --- test helpers -----------------------------------------------------

    def rendered_text(self) -> str:
        """Concatenate all visible text in the scroll, for tests."""
        scroll = self.query_one(VerticalScroll)
        parts: list[str] = []
        for child in scroll.children:
            if isinstance(child, Static):
                parts.append(str(child.content))
            else:
                # Recursively gather text from any nested widgets (tasks 6–9
                # introduce Collapsible-wrapped content).
                for static in child.query(Static):
                    parts.append(str(static.content))
        return "\n".join(parts)
