import asyncio
from pathlib import Path

import pytest
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
)

from patchbai.agents.fake_sdk_adapter import FakeSDKAdapter
from patchbai.agents.session import AgentSession
from patchbai.agents.state import AgentInfo
from patchbai.events import AgentMessageAppended, EventBus
from patchbai.persistence.transcript_store import AgentTranscript


def _info() -> AgentInfo:
    return AgentInfo(id="a1", name="lock-test", cwd="/tmp", started_at=100.0)


def _script(text: str) -> list:
    return [
        AssistantMessage(content=[TextBlock(text=text)], model="fake-model"),
        ResultMessage(
            subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
            num_turns=1, session_id="fake", total_cost_usd=0.0,
            usage={"input_tokens": 1, "output_tokens": 1}, result=text,
        ),
    ]


@pytest.mark.asyncio
async def test_concurrent_sends_are_serialized_and_both_complete(tmp_path: Path):
    bus = EventBus()
    appended: list[AgentMessageAppended] = []
    bus.subscribe(AgentMessageAppended, appended.append)

    adapter = FakeSDKAdapter(scripts=[_script("first reply"), _script("second reply")])
    session = AgentSession(
        info=_info(),
        adapter=adapter,
        transcript=AgentTranscript(cwd=tmp_path, agent_id="a1"),
        bus=bus,
    )
    await session.start(options=ClaudeAgentOptions())

    # Fire two sends back-to-back without waiting for the first to drain.
    t1 = asyncio.create_task(session.send("first"))
    t2 = asyncio.create_task(session.send("second"))
    await asyncio.gather(t1, t2)
    await session.wait_idle()

    user_texts = [a.text for a in appended if a.role == "user"]
    assistant_texts = [a.text for a in appended if a.role == "assistant"]

    # Both user prompts and both assistant replies must appear, in order.
    assert user_texts == ["first", "second"]
    assert assistant_texts == ["first reply", "second reply"]
