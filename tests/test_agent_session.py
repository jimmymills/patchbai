import asyncio
from pathlib import Path

import pytest
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
    UserMessage,
)

from mod_tui.agents.fake_sdk_adapter import FakeSDKAdapter
from mod_tui.agents.session import AgentSession
from mod_tui.agents.state import AgentInfo, AgentState
from mod_tui.events import (
    AgentMessageAppended,
    AgentStateChanged,
    EventBus,
)
from mod_tui.persistence.transcript_store import AgentTranscript


def _info() -> AgentInfo:
    return AgentInfo(id="a1", name="research", cwd="/tmp", started_at=100.0)


def _ok_script() -> list:
    return [
        AssistantMessage(content=[TextBlock(text="hello")], model="fake-model"),
        ResultMessage(
            subtype="success",
            duration_ms=10,
            duration_api_ms=5,
            is_error=False,
            num_turns=1,
            session_id="fake-session",
            total_cost_usd=0.0042,
            usage={"input_tokens": 7, "output_tokens": 11},
            result="hello",
        ),
    ]


@pytest.mark.asyncio
async def test_session_publishes_state_changes_around_query(tmp_path: Path):
    bus = EventBus()
    states: list[AgentStateChanged] = []
    bus.subscribe(AgentStateChanged, states.append)

    adapter = FakeSDKAdapter(scripts=[_ok_script()])
    session = AgentSession(
        info=_info(),
        adapter=adapter,
        transcript=AgentTranscript(cwd=tmp_path, agent_id="a1"),
        bus=bus,
    )
    await session.start(options=ClaudeAgentOptions())
    await session.send("hi")
    await session.wait_idle()

    state_sequence = [(c.old_state, c.info.state) for c in states]
    # IDLE → RUNNING → DONE
    assert state_sequence == [
        (AgentState.IDLE, AgentState.RUNNING),
        (AgentState.RUNNING, AgentState.DONE),
    ]


@pytest.mark.asyncio
async def test_session_appends_assistant_text_to_transcript(tmp_path: Path):
    bus = EventBus()
    transcript = AgentTranscript(cwd=tmp_path, agent_id="a1")
    adapter = FakeSDKAdapter(scripts=[_ok_script()])
    session = AgentSession(
        info=_info(),
        adapter=adapter,
        transcript=transcript,
        bus=bus,
    )
    await session.start(options=ClaudeAgentOptions())
    await session.send("hi")
    await session.wait_idle()

    entries = transcript.read_all()
    assert any(e.role == "user" and e.text == "hi" for e in entries)
    assert any(e.role == "assistant" and e.text == "hello" for e in entries)


@pytest.mark.asyncio
async def test_session_publishes_message_appended_events(tmp_path: Path):
    bus = EventBus()
    appended: list[AgentMessageAppended] = []
    bus.subscribe(AgentMessageAppended, appended.append)

    adapter = FakeSDKAdapter(scripts=[_ok_script()])
    session = AgentSession(
        info=_info(),
        adapter=adapter,
        transcript=AgentTranscript(cwd=tmp_path, agent_id="a1"),
        bus=bus,
    )
    await session.start(options=ClaudeAgentOptions())
    await session.send("hi")
    await session.wait_idle()

    assert any(a.role == "user" and a.text == "hi" for a in appended)
    assert any(a.role == "assistant" and a.text == "hello" for a in appended)


@pytest.mark.asyncio
async def test_session_records_usage_from_result(tmp_path: Path):
    bus = EventBus()
    info = _info()
    adapter = FakeSDKAdapter(scripts=[_ok_script()])
    session = AgentSession(
        info=info,
        adapter=adapter,
        transcript=AgentTranscript(cwd=tmp_path, agent_id="a1"),
        bus=bus,
    )
    await session.start(options=ClaudeAgentOptions())
    await session.send("hi")
    await session.wait_idle()

    assert info.tokens_in == 7
    assert info.tokens_out == 11
    assert info.cost == pytest.approx(0.0042)
    assert info.state == AgentState.DONE
