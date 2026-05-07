from __future__ import annotations

import time
from pathlib import Path

from rich.markdown import Markdown as _RichMarkdown
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

_SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
_SPINNER_INTERVAL_S = 0.08


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
        self._spinner_idx = 0
        self._spinner_timer = None
        self._done = False
        self._args_static = Static(self._build_args_text())
        self._result_static = Static(Text("(running…)", style="dim"))
        super().__init__(
            self._args_static,
            self._result_static,
            title=self._build_running_title(),
            collapsed=False,
        )

    def on_mount(self) -> None:
        if self._done:
            return
        self._spinner_timer = self.set_interval(_SPINNER_INTERVAL_S, self._tick_spinner)

    def _tick_spinner(self) -> None:
        self._spinner_idx = (self._spinner_idx + 1) % len(_SPINNER_FRAMES)
        self.title = self._build_running_title()

    def _build_args_text(self) -> Text:
        line = Text()
        line.append("args: ", style="bold")
        line.append(self._args_text)
        return line

    def _build_running_title(self) -> str:
        # Truncated, plain-string title — Collapsible accepts str.
        # Escape user-provided text so brackets don't get parsed as markup.
        short = self._args_text if len(self._args_text) <= 60 else self._args_text[:57] + "…"
        return f"{_SPINNER_FRAMES[self._spinner_idx]} {_markup_escape(self.tool_name)}({_markup_escape(short)})"

    def _build_done_title(self, result_text: str, *, error: bool) -> str:
        marker = "✗" if error else "✓"
        short = result_text.replace("\n", " ")
        if len(short) > 80:
            short = short[:77] + "…"
        return f"{marker} {_markup_escape(self.tool_name)} → {_markup_escape(short)}"

    def attach_result(self, content_text: str, *, error: bool = False) -> None:
        self._done = True
        if self._spinner_timer is not None:
            self._spinner_timer.stop()
            self._spinner_timer = None
        body = Text()
        body.append("result: ", style="bold")
        body.append(content_text, style="red" if error else "")
        self._result_static.update(body)
        self.title = self._build_done_title(content_text, error=error)
        self.collapsed = True

    def mark_done(self) -> None:
        self._done = True
        if self._spinner_timer is not None:
            self._spinner_timer.stop()
            self._spinner_timer = None
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
        self._spinner_idx = 0
        self._spinner_timer = None
        super().__init__(
            self._body_static,
            title=self._build_running_title(),
            collapsed=False,
        )

    def _build_running_title(self) -> str:
        return f"{_SPINNER_FRAMES[self._spinner_idx]} Thinking…"

    def on_mount(self) -> None:
        if self._done:
            return
        self._spinner_timer = self.set_interval(_SPINNER_INTERVAL_S, self._tick_spinner)

    def _tick_spinner(self) -> None:
        self._spinner_idx = (self._spinner_idx + 1) % len(_SPINNER_FRAMES)
        self.title = self._build_running_title()

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
        if self._spinner_timer is not None:
            self._spinner_timer.stop()
            self._spinner_timer = None
        elapsed = time.monotonic() - self._started
        self.title = f"Thought for {elapsed:.1f}s"
        self.collapsed = True


class _ProcessGroup(Collapsible):
    """Outer fold around the thinking + tool widgets between the user
    prompt and the next assistant response.

    Lets the user hide the entire intermediate process behind one click,
    so a finished turn reads as 'prompt → final response' with the work
    one expand-step away.
    """

    DEFAULT_CSS = """
    _ProcessGroup {
        margin: 0;
    }
    """

    def __init__(self) -> None:
        # Inner Vertical we own, so add_step() has a stable mount target.
        # Collapsible itself doesn't expose a public API for dynamic content
        # mounting; passing _body as the only "contents" child gives us one.
        self._body = Vertical()
        self._pending_steps: list = []
        self._tool_count = 0
        self._started = time.monotonic()
        self._done = False
        self._spinner_idx = 0
        self._spinner_timer = None
        super().__init__(
            self._body,
            title=self._build_running_title(),
            collapsed=False,
        )

    def _build_running_title(self) -> str:
        return f"{_SPINNER_FRAMES[self._spinner_idx]} Working…"

    def on_mount(self) -> None:
        # _body is mounted via Collapsible.compose → Contents → _body. By
        # the time on_mount fires for us, _body may or may not be attached
        # yet; _flush_pending re-schedules itself if it isn't.
        self._flush_pending()
        if self._done:
            return
        self._spinner_timer = self.set_interval(_SPINNER_INTERVAL_S, self._tick)

    def _tick(self) -> None:
        self._spinner_idx = (self._spinner_idx + 1) % len(_SPINNER_FRAMES)
        self.title = self._build_running_title()

    def _flush_pending(self) -> None:
        if not self._pending_steps:
            return
        if self._body.is_attached:
            self._body.mount(*self._pending_steps)
            self._pending_steps.clear()
        else:
            self.call_after_refresh(self._flush_pending)

    def add_step(self, widget) -> None:
        if isinstance(widget, _ToolCall):
            self._tool_count += 1
        if self._body.is_attached:
            self._body.mount(widget)
            return
        self._pending_steps.append(widget)
        if self.is_attached:
            self.call_after_refresh(self._flush_pending)

    def mark_done(self) -> None:
        if self._done:
            return
        self._done = True
        if self._spinner_timer is not None:
            self._spinner_timer.stop()
            self._spinner_timer = None
        # Propagate done to pending children so they don't start spinners
        # when they finally mount inside a collapsed group.
        for child in self._pending_steps:
            mark = getattr(child, "mark_done", None)
            if mark is not None:
                mark()
        if self._pending_steps and self._body.is_attached:
            self._body.mount(*self._pending_steps)
            self._pending_steps.clear()
        elapsed = time.monotonic() - self._started
        if self._tool_count == 1:
            self.title = f"Process · 1 tool · {elapsed:.1f}s"
        elif self._tool_count > 1:
            self.title = f"Process · {self._tool_count} tools · {elapsed:.1f}s"
        else:
            self.title = f"Process · {elapsed:.1f}s"
        self.collapsed = True


class _AssistantBlock(Static):
    """Final assistant text rendered as markdown via Rich.

    Stores the original source on `_source` so rendered_text() (used by
    tests and any plain-text consumers) returns the markdown source rather
    than the renderable's repr.
    """

    DEFAULT_CSS = """
    _AssistantBlock {
        margin: 0;
        padding: 0;
    }
    """

    def __init__(self, source: str) -> None:
        self._source = source
        # code_theme="ansi_dark" keeps fenced-code blocks inside Textual's
        # palette instead of injecting a hard-coded background color.
        super().__init__(_RichMarkdown(source, code_theme="ansi_dark"))


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
        self._current_process: _ProcessGroup | None = None

    def compose(self) -> ComposeResult:
        line = Text()
        line.append("you: ", style="bold cyan")
        line.append(self._user_text)
        yield Static(line, classes="msg-user")

    def _close_thinking_group(self) -> None:
        self._current_thinking = None

    def _close_process_group(self) -> None:
        if self._current_process is not None:
            self._current_process.mark_done()
            self._current_process = None

    def _ensure_process_group(self) -> _ProcessGroup:
        if self._current_process is None:
            group = _ProcessGroup()
            self._current_process = group
            self.mount(group)
        return self._current_process

    def add_thinking(self, text: str) -> None:
        process = self._ensure_process_group()
        if self._current_thinking is None:
            group = _ThinkingGroup()
            self._current_thinking = group
            process.add_step(group)
        self._current_thinking.append(text)

    def add_tool_call(
        self, *, tool_id: str | None, tool_name: str | None, args_text: str,
    ) -> None:
        self._close_thinking_group()
        process = self._ensure_process_group()
        widget = _ToolCall(
            tool_id=tool_id, tool_name=tool_name, args_text=args_text,
        )
        self._tool_widgets[tool_id or id(widget)] = widget
        process.add_step(widget)

    def attach_tool_result(self, *, tool_id: str | None, content_text: str) -> None:
        self._close_thinking_group()
        widget = self._tool_widgets.get(tool_id) if tool_id else None
        if widget is None:
            # Old-transcript fallback or out-of-order: attach to the most
            # recent _ToolCall in this turn whose result hasn't been set.
            # We iterate _tool_widgets (insertion-ordered) instead of the
            # DOM because tool widgets may still be queued inside a process
            # group's _pending_steps and not yet attached.
            # NOTE: use .content (not .renderable) for this Textual version.
            for tw in reversed(list(self._tool_widgets.values())):
                if "(running…)" in str(tw._result_static.content):
                    widget = tw
                    break
        if widget is None:
            # Truly orphaned — mount a free-floating result line, inside the
            # active process group if one exists, else on the turn directly.
            line = Text()
            line.append("result (orphan): ", style="bold red")
            line.append(content_text)
            orphan = Static(line)
            if self._current_process is not None:
                self._current_process.add_step(orphan)
            else:
                self.mount(orphan)
            return
        # Naive error detection — refined in later tasks if needed.
        is_err = content_text.lower().startswith("error")
        widget.attach_result(content_text, error=is_err)

    def add_text(self, text: str) -> None:
        self._close_thinking_group()
        # Final-response text closes the current round of process steps;
        # any subsequent thinking/tools open a fresh _ProcessGroup.
        self._close_process_group()
        prefix = Text()
        prefix.append("claude:", style="bold")
        self.mount(Static(prefix, classes="msg-final-prefix"))
        self.mount(_AssistantBlock(text))

    def mark_done(self) -> None:
        self.remove_class("turn-running")
        self.add_class("turn-done")
        # query() recurses into _ProcessGroup, so this still finds tools and
        # thinking groups even when they live inside a process group.
        for tool in self.query(_ToolCall):
            tool.mark_done()
        for group in self.query(_ThinkingGroup):
            group.mark_done()
        for proc in self.query(_ProcessGroup):
            proc.mark_done()
        self._current_process = None

    def mark_error(self) -> None:
        self.remove_class("turn-running")
        self.add_class("turn-error")
        for tool in self.query(_ToolCall):
            tool.mark_done()
        for group in self.query(_ThinkingGroup):
            group.mark_done()
        for proc in self.query(_ProcessGroup):
            proc.mark_done()
        self._current_process = None

    def rendered_text(self) -> str:
        parts: list[str] = []
        for static in self.query(Static):
            if isinstance(static, _AssistantBlock):
                parts.append(static._source)
            else:
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
        border: round $surface-lighten-2;
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
        transcript_path: "Path | None" = None,
    ) -> None:
        super().__init__()
        self._agent_id = agent_id
        self._bus = event_bus
        self._transcript_path = transcript_path
        self._unsub_msg = lambda: None
        self._unsub_state = lambda: None
        self._unsub_switched = lambda: None
        self._current_turn: _TurnContainer | None = None

    @property
    def agent_id(self) -> str:
        """Public read-only accessor for the agent_id this transcript watches."""
        return self._agent_id

    def compose(self) -> ComposeResult:
        yield VerticalScroll()

    def on_mount(self) -> None:
        from mod_tui.events import OrchestratorSessionSwitched
        cwd: Path | None = getattr(self.app, "cwd", None)
        if self._transcript_path is not None:
            store = TranscriptStore(
                cwd=cwd or Path("."), agent_id=self._agent_id,
                path=self._transcript_path,
            )
            for entry in store.read_all():
                self._dispatch_entry(entry)
        elif cwd is not None:
            store = TranscriptStore(cwd=cwd, agent_id=self._agent_id)
            for entry in store.read_all():
                self._dispatch_entry(entry)
        if self._current_turn is not None:
            self._current_turn.mark_done()
            self._current_turn = None
        bus = self._bus or getattr(self.app, "event_bus", None)
        if bus is not None:
            self._unsub_msg = bus.subscribe(AgentMessageAppended, self._on_appended)
            self._unsub_state = bus.subscribe(AgentStateChanged, self._on_state_changed)
            self._unsub_switched = bus.subscribe(
                OrchestratorSessionSwitched, self._on_session_switched,
            )

    def on_unmount(self) -> None:
        self._unsub_msg()
        self._unsub_state()
        self._unsub_switched()

    def replace_source(self, transcript_path: Path) -> None:
        """Clear the scroll and replay from a new transcript path.

        Called when the orchestrator session is swapped via /reset or /resume.
        Live event filtering still keys off `agent_id` (unchanged).
        """
        self._transcript_path = transcript_path
        scroll = self.query_one(VerticalScroll)
        for child in list(scroll.children):
            child.remove()
        self._current_turn = None
        cwd = getattr(self.app, "cwd", None) or Path(".")
        store = TranscriptStore(
            cwd=cwd, agent_id=self._agent_id, path=transcript_path,
        )
        for entry in store.read_all():
            self._dispatch_entry(entry)
        if self._current_turn is not None:
            self._current_turn.mark_done()
            self._current_turn = None

    def _on_session_switched(self, event) -> None:
        # Filter by agent_id semantics: only the orchestrator transcript reacts.
        if self._agent_id != "orchestrator":
            return
        self.replace_source(Path(event.transcript_path))

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
        if self._current_turn is not None:
            # Defensive — and required for history replay where no state event
            # closes a previous turn.
            self._current_turn.mark_done()
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

    @classmethod
    def default_border_title(cls, props: dict) -> str:
        agent_id = props.get("agent_id")
        if agent_id:
            return f"Transcript: {agent_id}"
        return "Transcript"
