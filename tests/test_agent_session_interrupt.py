"""Tests for AgentSession.interrupt — particularly the case where a
DirectMessageToAgent has been queued (and is blocked behind the current
stream) at the moment ctrl+c interrupts the session. Without explicit
cancellation, the queued send would wake up the moment the SDK signals
end-of-stream and run the queued prompt against the now-interrupted
session — which is how the orchestrator's `send_to_agent` payload was
landing on the child agent after the user pressed ctrl+c."""

import asyncio
from typing import AsyncIterator

import pytest
from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ResultMessage, TextBlock

from patchfeld.agents.session import AgentSession
from patchfeld.agents.state import AgentInfo
from patchfeld.events import EventBus
from patchfeld.persistence.transcript_store import AgentTranscript


def _info() -> AgentInfo:
    return AgentInfo(id="a1", name="x", cwd="/tmp", started_at=100.0)


class _ControllableAdapter:
    """Fake SDKAdapter that lets the test control when each stream ends.

    The real SDK's `interrupt()` causes the in-flight stream to wind down
    (the CLI emits a final ResultMessage, then `stream()` raises
    StopAsyncIteration). This fake mirrors that: `interrupt()` sets the
    end-of-stream event for the currently-iterating stream so tests can
    deterministically advance past the await-stream-task gate inside
    `AgentSession.send`."""

    def __init__(self) -> None:
        self.queries: list[str] = []
        self._end_event = asyncio.Event()
        self.interrupt_called = False

    async def start(self, *, options: ClaudeAgentOptions) -> None:
        return None

    async def query(self, prompt: str) -> None:
        self.queries.append(prompt)
        # Reset the event for this query's stream so previous interrupts
        # don't immediately end the new stream.
        self._end_event = asyncio.Event()

    def stream(self) -> AsyncIterator[object]:
        end_event = self._end_event

        async def _agen() -> AsyncIterator[object]:
            # First yield an assistant block so the session records "running".
            yield AssistantMessage(
                content=[TextBlock(text="streaming…")], model="fake",
            )
            # Then block until the test (or interrupt) signals end-of-stream.
            await end_event.wait()
            yield ResultMessage(
                subtype="success", duration_ms=1, duration_api_ms=1,
                is_error=False, num_turns=1, session_id="fake",
                total_cost_usd=0.0, usage={"input_tokens": 1, "output_tokens": 1},
                result="ended",
            )

        return _agen()

    async def interrupt(self) -> None:
        self.interrupt_called = True
        # Real SDK behavior: interrupt causes the active stream to wind
        # down and end. Mirror that so the test exercises the post-stream
        # path that previously let queued sends slip through.
        self._end_event.set()

    async def stop(self) -> None:
        return None


@pytest.mark.asyncio
async def test_interrupt_cancels_pending_queued_send(tmp_path):
    """A send queued behind the active stream must NOT run after interrupt.

    Reproduces the user-reported bug: orchestrator's `send_to_agent` had
    queued a DirectMessageToAgent on the child while the child was
    mid-thinking. ctrl+c interrupted the child's current turn, which
    let the queued send wake up and post the orchestrator's command as
    the next user message — so the agent ran the orchestrator's command
    instead of stopping."""

    bus = EventBus()
    adapter = _ControllableAdapter()
    session = AgentSession(
        info=_info(),
        adapter=adapter,
        transcript=AgentTranscript(cwd=tmp_path, agent_id="a1"),
        bus=bus,
    )
    await session.start(options=ClaudeAgentOptions())

    # 1. Send the first prompt — starts streaming, parks on the end_event.
    await session.send("first prompt from user")
    # Let the stream task start consuming so it's actually awaiting the event.
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    # 2. Queue a second send while the first stream is still open.
    #    This task blocks on `await self._stream_task` inside send().
    queued_task = session.queue_send("orchestrator's payload that must NOT run")
    # Give the queued task a tick to enter `send()` and reach the await.
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert not queued_task.done(), "queued send should be blocked on the active stream"

    # 3. Interrupt — should cancel the queued task AND signal the SDK.
    await session.interrupt()
    # Drain any scheduled callbacks.
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    # 4. The queued send must NOT have reached adapter.query.
    assert adapter.queries == ["first prompt from user"], (
        f"adapter received unexpected queries after interrupt: {adapter.queries}"
    )
    assert adapter.interrupt_called is True
    # Queued task is done (cancelled or returned). Either way it must not
    # have produced a query.
    if not queued_task.done():
        # Give it one more chance to settle so the test is deterministic.
        try:
            await asyncio.wait_for(queued_task, timeout=0.5)
        except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
            pass
    assert queued_task.done()
    assert adapter.queries == ["first prompt from user"]
