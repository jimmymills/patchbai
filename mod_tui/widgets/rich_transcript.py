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


class _TurnContainer(Vertical):
    """One conversation turn: user prompt + steps + final response."""

    DEFAULT_CSS = """
    _TurnContainer {
        height: auto;
        margin-top: 1;
    }
    _TurnContainer.turn-running {
        border-left: thick $accent;
        padding-left: 1;
    }
    _TurnContainer.turn-done,
    _TurnContainer.turn-error {
        padding-left: 1;
    }
    _TurnContainer.turn-error {
        border-left: thick $error;
    }
    """

    def __init__(self, user_text: str) -> None:
        super().__init__()
        self.add_class("turn-running")
        self._user_text = user_text

    def compose(self) -> ComposeResult:
        line = Text()
        line.append("you: ", style="bold cyan")
        line.append(self._user_text)
        yield Static(line, classes="msg-user")

    def add_thinking(self, text: str) -> None:
        line = Text()
        line.append("thinking: ", style="bold dim")
        line.append(text, style="dim")
        self.mount(Static(line, classes="msg-thinking"))

    def add_tool_call(
        self, *, tool_id: str | None, tool_name: str | None, args_text: str,
    ) -> None:
        line = Text()
        line.append(f"tool[{tool_name or '?'}]: ", style="bold yellow")
        line.append(args_text)
        widget = Static(line, classes="msg-tool-use")
        self.mount(widget)

    def attach_tool_result(self, *, tool_id: str | None, content_text: str) -> None:
        line = Text()
        line.append("result: ", style="bold")
        line.append(content_text)
        self.mount(Static(line, classes="msg-tool-result"))

    def add_text(self, text: str) -> None:
        line = Text()
        line.append("claude: ", style="bold")
        line.append(text)
        self.mount(Static(line, classes="msg-final"))

    def mark_done(self) -> None:
        self.remove_class("turn-running")
        self.add_class("turn-done")

    def mark_error(self) -> None:
        self.remove_class("turn-running")
        self.add_class("turn-error")

    def rendered_text(self) -> str:
        parts: list[str] = []
        for static in self.query(Static):
            parts.append(str(static.content))
        return "\n".join(parts)


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
        self._current_turn: _TurnContainer | None = None

    def compose(self) -> ComposeResult:
        yield VerticalScroll()

    def on_mount(self) -> None:
        cwd: Path | None = getattr(self.app, "cwd", None)
        if cwd is not None:
            store = TranscriptStore(cwd=cwd, agent_id=self._agent_id)
            for entry in store.read_all():
                self._dispatch_entry(entry)
        bus = self._bus or getattr(self.app, "event_bus", None)
        if bus is not None:
            self._unsub_msg = bus.subscribe(AgentMessageAppended, self._on_appended)

    def on_unmount(self) -> None:
        self._unsub_msg()

    def _on_appended(self, event: AgentMessageAppended) -> None:
        if event.agent_id != self._agent_id:
            return
        self._dispatch_entry(TranscriptEntry(
            role=event.role, text=event.text,
            tool_id=event.tool_id, tool_name=event.tool_name,
        ))

    def _dispatch_entry(self, entry: TranscriptEntry) -> None:
        if entry.role == "user":
            self._open_turn(entry.text)
            return
        if self._current_turn is None:
            # Defensive: a non-user entry arrived before any user entry.
            # Open a synthetic empty turn so the entry has somewhere to live.
            self._open_turn("")
        turn = self._current_turn
        assert turn is not None
        if entry.role == "assistant":
            turn.add_text(entry.text)
        elif entry.role == "thinking":
            turn.add_thinking(entry.text)
        elif entry.role == "tool_use":
            turn.add_tool_call(
                tool_id=entry.tool_id, tool_name=entry.tool_name,
                args_text=entry.text,
            )
        elif entry.role == "tool_result":
            turn.attach_tool_result(
                tool_id=entry.tool_id, content_text=entry.text,
            )

    def _open_turn(self, user_text: str) -> None:
        scroll = self.query_one(VerticalScroll)
        turn = _TurnContainer(user_text=user_text)
        self._current_turn = turn
        scroll.mount(turn)
        scroll.scroll_end(animate=False)

    # --- test helpers -----------------------------------------------------

    def rendered_text(self) -> str:
        """Concatenate all visible text in the scroll, for tests."""
        scroll = self.query_one(VerticalScroll)
        parts: list[str] = []
        for child in scroll.children:
            if isinstance(child, _TurnContainer):
                parts.append(child.rendered_text())
            elif isinstance(child, Static):
                parts.append(str(child.content))
        return "\n".join(parts)
