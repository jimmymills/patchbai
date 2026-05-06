from __future__ import annotations

import time
from pathlib import Path

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.markup import escape as _markup_escape
from textual.widgets import Collapsible, Static

from mod_tui.agents.state import AgentState
from mod_tui.events import AgentMessageAppended, AgentStateChanged, EventBus
from mod_tui.persistence.transcript_store import (
    AgentTranscript as TranscriptStore,
    TranscriptEntry,
)


class _ToolCall(Collapsible):
    """One tool invocation. Expanded while running, collapsed on result."""

    DEFAULT_CSS = """
    _ToolCall {
        margin: 0;
    }
    """

    def __init__(self, *, tool_id: str | None, tool_name: str | None, args_text: str) -> None:
        self.tool_id = tool_id
        self.tool_name = tool_name or "?"
        self._args_text = args_text
        self._args_static = Static(self._build_args_text())
        self._result_static = Static(Text("(running…)", style="dim"))
        super().__init__(
            self._args_static,
            self._result_static,
            title=self._build_running_title(),
            collapsed=False,
        )

    def _build_args_text(self) -> Text:
        line = Text()
        line.append("args: ", style="bold")
        line.append(self._args_text)
        return line

    def _build_running_title(self) -> str:
        # Truncated, plain-string title — Collapsible accepts str.
        # Escape user-provided text so brackets don't get parsed as markup.
        short = self._args_text if len(self._args_text) <= 60 else self._args_text[:57] + "…"
        return f"… {_markup_escape(self.tool_name)}({_markup_escape(short)})"

    def _build_done_title(self, result_text: str, *, error: bool) -> str:
        marker = "✗" if error else "✓"
        short = result_text.replace("\n", " ")
        if len(short) > 80:
            short = short[:77] + "…"
        return f"{marker} {_markup_escape(self.tool_name)} → {_markup_escape(short)}"

    def attach_result(self, content_text: str, *, error: bool = False) -> None:
        body = Text()
        body.append("result: ", style="bold")
        body.append(content_text, style="red" if error else "")
        self._result_static.update(body)
        self.title = self._build_done_title(content_text, error=error)
        self.collapsed = True

    def mark_done(self) -> None:
        # Called when the turn ends. If no result ever attached (shouldn't
        # normally happen), still collapse the foldable.
        # NOTE: use .content (not .renderable) for this Textual version.
        if self._result_static.content and "(running…)" in str(self._result_static.content):
            self._result_static.update(Text("(no result received)", style="dim red"))
            self.title = f"? {_markup_escape(self.tool_name)} (no result)"
        self.collapsed = True


class _ThinkingGroup(Collapsible):
    """A contiguous run of thinking blocks. Expanded while running."""

    DEFAULT_CSS = """
    _ThinkingGroup {
        margin: 0;
    }
    """

    def __init__(self) -> None:
        self._body_static = Static(Text(""))
        self._started = time.monotonic()
        self._done = False
        super().__init__(
            self._body_static,
            title="Thinking…",
            collapsed=False,
        )

    def append(self, text: str) -> None:
        existing = self._body_static.content
        body = existing if isinstance(existing, Text) else Text(str(existing))
        if len(body) > 0:
            body.append("\n")
        body.append(text, style="dim")
        self._body_static.update(body)

    def mark_done(self) -> None:
        if self._done:
            return
        self._done = True
        elapsed = time.monotonic() - self._started
        self.title = f"Thought for {elapsed:.1f}s"
        self.collapsed = True


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
        self._tool_widgets: dict = {}
        self._current_thinking: _ThinkingGroup | None = None

    def compose(self) -> ComposeResult:
        line = Text()
        line.append("you: ", style="bold cyan")
        line.append(self._user_text)
        yield Static(line, classes="msg-user")

    def _close_thinking_group(self) -> None:
        self._current_thinking = None

    def add_thinking(self, text: str) -> None:
        if self._current_thinking is None:
            group = _ThinkingGroup()
            self._current_thinking = group
            self.mount(group)
        self._current_thinking.append(text)

    def add_tool_call(
        self, *, tool_id: str | None, tool_name: str | None, args_text: str,
    ) -> None:
        self._close_thinking_group()
        widget = _ToolCall(
            tool_id=tool_id, tool_name=tool_name, args_text=args_text,
        )
        self._tool_widgets[tool_id or id(widget)] = widget
        self.mount(widget)

    def attach_tool_result(self, *, tool_id: str | None, content_text: str) -> None:
        self._close_thinking_group()
        widget = self._tool_widgets.get(tool_id) if tool_id else None
        if widget is None:
            # Old transcript fallback or out-of-order: attach to most-recent
            # _ToolCall whose result hasn't been set yet.
            for w in reversed(list(self.query(_ToolCall))):
                # NOTE: use .content (not .renderable) for this Textual version.
                if "(running…)" in str(w._result_static.content):
                    widget = w
                    break
        if widget is None:
            # Truly orphaned — mount a free-floating result line.
            line = Text()
            line.append("result (orphan): ", style="bold red")
            line.append(content_text)
            self.mount(Static(line))
            return
        # Naive error detection — refined in later tasks if needed.
        is_err = content_text.lower().startswith("error")
        widget.attach_result(content_text, error=is_err)

    def add_text(self, text: str) -> None:
        self._close_thinking_group()
        line = Text()
        line.append("claude: ", style="bold")
        line.append(text)
        self.mount(Static(line, classes="msg-final"))

    def mark_done(self) -> None:
        self.remove_class("turn-running")
        self.add_class("turn-done")
        for tool in self.query(_ToolCall):
            tool.mark_done()
        for group in self.query(_ThinkingGroup):
            group.mark_done()

    def mark_error(self) -> None:
        self.remove_class("turn-running")
        self.add_class("turn-error")
        for tool in self.query(_ToolCall):
            tool.mark_done()
        for group in self.query(_ThinkingGroup):
            group.mark_done()

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
        self._unsub_state = lambda: None
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
            self._unsub_state = bus.subscribe(AgentStateChanged, self._on_state_changed)

    def on_unmount(self) -> None:
        self._unsub_msg()
        self._unsub_state()

    def _on_appended(self, event: AgentMessageAppended) -> None:
        if event.agent_id != self._agent_id:
            return
        self._dispatch_entry(TranscriptEntry(
            role=event.role, text=event.text,
            tool_id=event.tool_id, tool_name=event.tool_name,
        ))

    def _on_state_changed(self, event: AgentStateChanged) -> None:
        if event.info.id != self._agent_id:
            return
        if self._current_turn is None:
            return
        if event.info.state == AgentState.DONE:
            self._current_turn.mark_done()
            self._current_turn = None
        elif event.info.state == AgentState.ERROR:
            self._current_turn.mark_error()
            self._current_turn = None

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
