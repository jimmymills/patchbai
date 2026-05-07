import asyncio
from pathlib import Path

import pytest
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
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


@pytest.mark.asyncio
async def test_tool_use_and_tool_result_carry_tool_id(tmp_path):
    """ToolUseBlock.id and ToolResultBlock.tool_use_id reach the bus event."""
    bus = EventBus()
    received: list[AgentMessageAppended] = []
    bus.subscribe(AgentMessageAppended, received.append)

    script = [
        AssistantMessage(
            content=[ToolUseBlock(id="toolu_xyz", name="bash",
                                  input={"command": "ls"})],
            model="fake-model",
        ),
        UserMessage(content=[ToolResultBlock(
            tool_use_id="toolu_xyz", content="output", is_error=False,
        )]),
        ResultMessage(
            subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
            num_turns=1, session_id="fake", total_cost_usd=0.0,
            usage={"input_tokens": 1, "output_tokens": 1}, result="ok",
        ),
    ]

    session = AgentSession(
        info=AgentInfo(id="a1", name="a1", cwd=str(tmp_path), started_at=0),
        adapter=FakeSDKAdapter(scripts=[script]),
        transcript=AgentTranscript(cwd=tmp_path, agent_id="a1"),
        bus=bus,
    )
    await session.start(options=ClaudeAgentOptions(cwd=str(tmp_path)))
    await session.send("go")
    await session.wait_idle()
    await session.stop()

    tool_uses = [e for e in received if e.role == "tool_use"]
    tool_results = [e for e in received if e.role == "tool_result"]
    assert tool_uses and tool_uses[0].tool_id == "toolu_xyz"
    assert tool_uses[0].tool_name == "bash"
    assert tool_results and tool_results[0].tool_id == "toolu_xyz"
    assert tool_results[0].tool_name is None


@pytest.mark.asyncio
async def test_session_exposes_sdk_session_id_after_first_result(tmp_path):
    bus = EventBus()
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
    assert session.session_id == "fake-session"


@pytest.mark.asyncio
async def test_session_on_session_id_fires_once(tmp_path):
    bus = EventBus()
    adapter = FakeSDKAdapter(scripts=[_ok_script(), _ok_script()])
    seen: list[str] = []
    session = AgentSession(
        info=_info(),
        adapter=adapter,
        transcript=AgentTranscript(cwd=tmp_path, agent_id="a1"),
        bus=bus,
        on_session_id=seen.append,
    )
    await session.start(options=ClaudeAgentOptions())
    await session.send("hi")
    await session.wait_idle()
    await session.send("again")
    await session.wait_idle()
    assert seen == ["fake-session"]


@pytest.mark.asyncio
async def test_session_id_is_none_before_first_result(tmp_path):
    bus = EventBus()
    adapter = FakeSDKAdapter(scripts=[_ok_script()])
    session = AgentSession(
        info=_info(),
        adapter=adapter,
        transcript=AgentTranscript(cwd=tmp_path, agent_id="a1"),
        bus=bus,
    )
    await session.start(options=ClaudeAgentOptions())
    assert session.session_id is None


@pytest.mark.asyncio
async def test_mark_waiting_transitions_running_to_waiting(tmp_path):
    bus = EventBus()
    transitions: list[AgentStateChanged] = []
    bus.subscribe(AgentStateChanged, transitions.append)

    session = AgentSession(
        info=AgentInfo(
            id="a1", name="a1", cwd=str(tmp_path),
            started_at=0.0, state=AgentState.RUNNING,
        ),
        adapter=FakeSDKAdapter(scripts=[]),
        transcript=AgentTranscript(cwd=tmp_path, agent_id="a1"),
        bus=bus,
    )

    session._mark_waiting()
    assert session.info.state == AgentState.WAITING
    assert transitions[-1].old_state == AgentState.RUNNING
    assert transitions[-1].info.state == AgentState.WAITING


@pytest.mark.asyncio
async def test_mark_unwaiting_restores_pre_wait_state(tmp_path):
    bus = EventBus()
    session = AgentSession(
        info=AgentInfo(
            id="a1", name="a1", cwd=str(tmp_path),
            started_at=0.0, state=AgentState.RUNNING,
        ),
        adapter=FakeSDKAdapter(scripts=[]),
        transcript=AgentTranscript(cwd=tmp_path, agent_id="a1"),
        bus=bus,
    )

    session._mark_waiting()
    session._mark_unwaiting()
    assert session.info.state == AgentState.RUNNING


@pytest.mark.asyncio
async def test_mark_waiting_is_idempotent_for_stacked_calls(tmp_path):
    """Two enters then two exits round-trip cleanly."""
    bus = EventBus()
    session = AgentSession(
        info=AgentInfo(
            id="a1", name="a1", cwd=str(tmp_path),
            started_at=0.0, state=AgentState.RUNNING,
        ),
        adapter=FakeSDKAdapter(scripts=[]),
        transcript=AgentTranscript(cwd=tmp_path, agent_id="a1"),
        bus=bus,
    )

    session._mark_waiting()
    session._mark_waiting()  # second enter is a no-op
    assert session.info.state == AgentState.WAITING
    session._mark_unwaiting()
    assert session.info.state == AgentState.RUNNING


@pytest.mark.asyncio
async def test_mark_unwaiting_when_not_waiting_is_noop(tmp_path):
    bus = EventBus()
    session = AgentSession(
        info=AgentInfo(
            id="a1", name="a1", cwd=str(tmp_path),
            started_at=0.0, state=AgentState.RUNNING,
        ),
        adapter=FakeSDKAdapter(scripts=[]),
        transcript=AgentTranscript(cwd=tmp_path, agent_id="a1"),
        bus=bus,
    )

    session._mark_unwaiting()
    assert session.info.state == AgentState.RUNNING


@pytest.mark.asyncio
async def test_mark_unwaiting_does_not_resurrect_terminal_state(tmp_path):
    bus = EventBus()
    session = AgentSession(
        info=AgentInfo(
            id="a1", name="a1", cwd=str(tmp_path),
            started_at=0.0, state=AgentState.RUNNING,
        ),
        adapter=FakeSDKAdapter(scripts=[]),
        transcript=AgentTranscript(cwd=tmp_path, agent_id="a1"),
        bus=bus,
    )

    session._mark_waiting()
    # Simulate the stream ending while waiting — defensive only; the real
    # SDK would never do this because the tool result is still pending.
    session.info.state = AgentState.DONE
    session._mark_unwaiting()
    assert session.info.state == AgentState.DONE
