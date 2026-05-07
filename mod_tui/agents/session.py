import asyncio
import dataclasses
import time
from typing import Callable, Iterable

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from mod_tui.agents.sdk_adapter import SDKAdapter
from mod_tui.agents.state import AgentInfo, AgentState
from mod_tui.events import (
    AgentMessageAppended,
    AgentStateChanged,
    AgentTokensTouched,
    EventBus,
)
from mod_tui.persistence.transcript_store import AgentTranscript, TranscriptEntry


class AgentSession:
    """One Claude Agent SDK session: one adapter, one transcript, one state machine."""

    def __init__(
        self,
        *,
        info: AgentInfo,
        adapter: SDKAdapter,
        transcript: AgentTranscript,
        bus: EventBus,
        on_session_id: "Callable[[str], None] | None" = None,
    ) -> None:
        self.info = info
        self._adapter = adapter
        self._transcript = transcript
        self._bus = bus
        self._on_session_id = on_session_id
        self._session_id: str | None = None
        self._stream_task: asyncio.Task | None = None
        self._idle_event = asyncio.Event()
        self._idle_event.set()
        self._send_lock = asyncio.Lock()
        self._pre_wait_state: AgentState | None = None

    @property
    def session_id(self) -> str | None:
        return self._session_id

    async def start(self, *, options: ClaudeAgentOptions) -> None:
        await self._adapter.start(options=options)

    async def send(self, prompt: str) -> None:
        async with self._send_lock:
            # If the previous stream is still draining, wait for it before
            # issuing the next query — the SDK doesn't support overlapping
            # query() calls on a single session.
            if self._stream_task is not None and not self._stream_task.done():
                await self._stream_task

            self._record(role="user", text=prompt)
            await self._adapter.query(prompt)
            self._set_state(AgentState.RUNNING)
            self._idle_event.clear()
            self._stream_task = asyncio.create_task(self._consume_stream())

    def queue_send(self, prompt: str) -> "asyncio.Task":
        """Schedule a send() on the running event loop and return the Task.

        Eagerly clears `_idle_event` synchronously so a subsequent wait_idle()
        in the same task will correctly block until the send completes —
        without it, wait_idle could return before the send task acquires the
        send lock.
        """
        self._idle_event.clear()
        return asyncio.create_task(self.send(prompt))

    async def wait_idle(self) -> None:
        await self._idle_event.wait()

    async def interrupt(self) -> None:
        await self._adapter.interrupt()

    async def stop(self) -> None:
        if self._stream_task is not None and not self._stream_task.done():
            self._stream_task.cancel()
            try:
                await self._stream_task
            except (asyncio.CancelledError, Exception):
                pass
        await self._adapter.stop()

    # --- internals --------------------------------------------------------

    async def _consume_stream(self) -> None:
        try:
            async for msg in self._adapter.stream():
                self._handle_message(msg)
            self._set_state(AgentState.DONE)
        except Exception:
            self._set_state(AgentState.ERROR)
        finally:
            self._idle_event.set()

    def _handle_message(self, msg: object) -> None:
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, TextBlock):
                    self._record(role="assistant", text=block.text)
                elif isinstance(block, ToolUseBlock):
                    self._record(
                        role="tool_use",
                        text=f"[{block.name}] {_short_repr(block.input)}",
                        tool_id=block.id,
                        tool_name=block.name,
                    )
                elif isinstance(block, ThinkingBlock):
                    self._record(role="thinking", text=block.thinking)
        elif isinstance(msg, UserMessage):
            for block in msg.content:
                if isinstance(block, ToolResultBlock):
                    # tool_name not available on result blocks; consumers match via tool_id
                    self._record(
                        role="tool_result",
                        text=_short_repr(block.content),
                        tool_id=block.tool_use_id,
                    )
        elif isinstance(msg, SystemMessage):
            # Skip — verbose protocol noise.
            pass
        elif isinstance(msg, ResultMessage):
            if self._session_id is None and msg.session_id:
                self._session_id = msg.session_id
                if self._on_session_id is not None:
                    try:
                        self._on_session_id(msg.session_id)
                    except Exception:
                        # Callback errors must not poison the SDK stream.
                        pass
            usage = msg.usage or {}
            tokens_in = int(usage.get("input_tokens", 0) or 0)
            tokens_out = int(usage.get("output_tokens", 0) or 0)
            self.info.tokens_in += tokens_in
            self.info.tokens_out += tokens_out
            if msg.total_cost_usd is not None:
                self.info.cost += float(msg.total_cost_usd)
            if tokens_in or tokens_out or msg.total_cost_usd:
                self._bus.publish(AgentTokensTouched(agent_id=self.info.id))
        self.info.last_activity = time.time()

    def _record(
        self,
        *,
        role: str,
        text: str,
        tool_id: str | None = None,
        tool_name: str | None = None,
    ) -> None:
        entry = TranscriptEntry(
            role=role, text=text, tool_id=tool_id, tool_name=tool_name,
        )
        self._transcript.append(entry)
        self._bus.publish(
            AgentMessageAppended(
                agent_id=self.info.id, role=role, text=text,
                tool_id=tool_id, tool_name=tool_name,
            )
        )
        self.info.last_activity = time.time()

    def _mark_waiting(self) -> None:
        """Enter WAITING state, snapshotting the prior state for restore.

        Idempotent: a second call while already WAITING is a no-op (the
        snapshot is preserved). Skipped if the session is already in a
        terminal state.
        """
        if self.info.state.is_terminal:
            return
        if self.info.state == AgentState.WAITING:
            return
        self._pre_wait_state = self.info.state
        self._set_state(AgentState.WAITING)

    def _mark_unwaiting(self) -> None:
        """Exit WAITING state, restoring the pre-wait state.

        No-op when not in WAITING. If the session is somehow terminal,
        the snapshot is dropped without a transition.
        """
        if self.info.state != AgentState.WAITING:
            self._pre_wait_state = None
            return
        target = self._pre_wait_state or AgentState.RUNNING
        self._pre_wait_state = None
        if target.is_terminal:
            # Defensive: never resurrect a terminal state.
            return
        self._set_state(target)

    def _set_state(self, new_state: AgentState) -> None:
        old = self.info.state
        if old == new_state:
            return
        self.info.state = new_state
        if new_state.is_terminal:
            self.info.ended_at = time.time()
        # Publish a frozen snapshot so subscribers see the state at publish time,
        # not a live reference that may mutate before they inspect it.
        self._bus.publish(
            AgentStateChanged(info=dataclasses.replace(self.info), old_state=old)
        )


def _short_repr(value: object, limit: int = 200) -> str:
    s = repr(value)
    return s if len(s) <= limit else s[: limit - 1] + "…"
