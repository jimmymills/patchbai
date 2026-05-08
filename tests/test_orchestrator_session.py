import pytest
from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from patchfeld.agents.fake_sdk_adapter import FakeSDKAdapter
from patchfeld.agents.manager import AgentManager
from patchfeld.events import EventBus, OrchestratorReply, UserMessageToOrchestrator
from patchfeld.orchestrator.session import OrchestratorSession
from patchfeld.persistence.transcript_store import AgentTranscript


def _ok_script() -> list:
    return [
        AssistantMessage(content=[TextBlock(text="hello, world")], model="fake-model"),
        ResultMessage(
            subtype="success",
            duration_ms=1,
            duration_api_ms=1,
            is_error=False,
            num_turns=1,
            session_id="fake",
            total_cost_usd=0.0,
            usage={"input_tokens": 1, "output_tokens": 1},
            result="hello, world",
        ),
    ]


@pytest.mark.asyncio
async def test_orchestrator_session_publishes_reply_for_user_message(tmp_path):
    bus = EventBus()
    replies: list[OrchestratorReply] = []
    bus.subscribe(OrchestratorReply, replies.append)

    manager = AgentManager(
        cwd=tmp_path,
        bus=bus,
        adapter_factory=lambda: FakeSDKAdapter(scripts=[_ok_script()]),
    )
    session = OrchestratorSession(
        cwd=tmp_path,
        bus=bus,
        manager=manager,
        adapter=FakeSDKAdapter(scripts=[_ok_script()]),
    )
    await session.start()

    bus.publish(UserMessageToOrchestrator("ping"))
    await session.wait_idle()

    assert any(r.text == "hello, world" for r in replies)


@pytest.mark.asyncio
async def test_orchestrator_session_records_transcript(tmp_path):
    bus = EventBus()
    manager = AgentManager(
        cwd=tmp_path,
        bus=bus,
        adapter_factory=lambda: FakeSDKAdapter(scripts=[_ok_script()]),
    )
    session = OrchestratorSession(
        cwd=tmp_path,
        bus=bus,
        manager=manager,
        adapter=FakeSDKAdapter(scripts=[_ok_script()]),
    )
    await session.start()

    bus.publish(UserMessageToOrchestrator("ping"))
    await session.wait_idle()

    transcript_path = session._active_transcript_path
    entries = AgentTranscript(cwd=tmp_path, agent_id="orchestrator", path=transcript_path).read_all()
    assert any(e.role == "user" and e.text == "ping" for e in entries)
    assert any(e.role == "assistant" and e.text == "hello, world" for e in entries)


@pytest.mark.asyncio
async def test_orchestrator_session_stop_unsubscribes(tmp_path):
    bus = EventBus()
    replies: list[OrchestratorReply] = []
    bus.subscribe(OrchestratorReply, replies.append)

    manager = AgentManager(
        cwd=tmp_path,
        bus=bus,
        adapter_factory=lambda: FakeSDKAdapter(scripts=[_ok_script()]),
    )
    session = OrchestratorSession(
        cwd=tmp_path,
        bus=bus,
        manager=manager,
        adapter=FakeSDKAdapter(scripts=[_ok_script()]),
    )
    await session.start()
    await session.stop()

    bus.publish(UserMessageToOrchestrator("after stop"))

    assert replies == []  # nothing fired after stop


@pytest.mark.asyncio
async def test_tool_use_does_not_publish_orchestrator_reply(tmp_path):
    """Tool use/result no longer go through OrchestratorReply — RichTranscript
    reads the richer AgentMessageAppended event directly."""
    script = [
        AssistantMessage(
            content=[ToolUseBlock(id="t1", name="bash", input={"cmd": "ls"})],
            model="fake-model",
        ),
        UserMessage(content=[ToolResultBlock(
            tool_use_id="t1", content="ok", is_error=False,
        )]),
        AssistantMessage(content=[TextBlock(text="done")], model="fake-model"),
        ResultMessage(
            subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
            num_turns=1, session_id="fake", total_cost_usd=0.0,
            usage={"input_tokens": 1, "output_tokens": 1}, result="done",
        ),
    ]

    bus = EventBus()
    replies: list[OrchestratorReply] = []
    bus.subscribe(OrchestratorReply, replies.append)

    manager = AgentManager(
        cwd=tmp_path, bus=bus,
        adapter_factory=lambda: FakeSDKAdapter(scripts=[_ok_script()]),
    )
    session = OrchestratorSession(
        cwd=tmp_path, bus=bus, manager=manager,
        adapter=FakeSDKAdapter(scripts=[script]),
    )
    await session.start()
    bus.publish(UserMessageToOrchestrator("go"))
    await session.wait_idle()
    await session.stop()

    reply_texts = [r.text for r in replies]
    # Assistant text still comes through — preserves existing behavior.
    assert "done" in reply_texts
    # Tool use/result no longer leak through the reply channel.
    assert not any(t.startswith("[tool use]") for t in reply_texts)
    assert not any(t.startswith("[tool result]") for t in reply_texts)
