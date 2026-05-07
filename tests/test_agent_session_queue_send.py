import pytest
from claude_agent_sdk import ClaudeAgentOptions

from patchbai.agents.fake_sdk_adapter import FakeSDKAdapter
from patchbai.agents.session import AgentSession
from patchbai.agents.state import AgentInfo
from patchbai.events import EventBus
from patchbai.persistence.transcript_store import AgentTranscript


def _info() -> AgentInfo:
    return AgentInfo(id="a1", name="x", cwd="/tmp", started_at=100.0)


@pytest.mark.asyncio
async def test_queue_send_returns_a_task_that_completes(tmp_path, ok_script):
    bus = EventBus()
    adapter = FakeSDKAdapter(scripts=[ok_script("hi")])
    session = AgentSession(
        info=_info(),
        adapter=adapter,
        transcript=AgentTranscript(cwd=tmp_path, agent_id="a1"),
        bus=bus,
    )
    await session.start(options=ClaudeAgentOptions())

    task = session.queue_send("hello")
    assert not task.done(), "queue_send must return a not-yet-done task"
    await task
    await session.wait_idle()

    entries = session._transcript.read_all()
    assert any(e.role == "user" and e.text == "hello" for e in entries)


@pytest.mark.asyncio
async def test_queue_send_eagerly_clears_idle_event(tmp_path, ok_script):
    """wait_idle() right after queue_send() must block until the task completes."""
    bus = EventBus()
    adapter = FakeSDKAdapter(scripts=[ok_script("hi")])
    session = AgentSession(
        info=_info(),
        adapter=adapter,
        transcript=AgentTranscript(cwd=tmp_path, agent_id="a1"),
        bus=bus,
    )
    await session.start(options=ClaudeAgentOptions())

    session.queue_send("hello")
    # If queue_send didn't eagerly clear the idle event, wait_idle could return
    # before the queued send has even acquired the send lock.
    await session.wait_idle()

    entries = session._transcript.read_all()
    assert any(e.role == "user" for e in entries), "user message must have been recorded"
