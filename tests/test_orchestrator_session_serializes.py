import asyncio

import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

from patchbai.agents.fake_sdk_adapter import FakeSDKAdapter
from patchbai.agents.manager import AgentManager
from patchbai.events import EventBus, OrchestratorReply, UserMessageToOrchestrator
from patchbai.orchestrator.session import OrchestratorSession


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
async def test_two_user_messages_in_quick_succession_both_get_replies(tmp_path):
    bus = EventBus()
    replies: list[OrchestratorReply] = []
    bus.subscribe(OrchestratorReply, replies.append)

    manager = AgentManager(
        cwd=tmp_path,
        bus=bus,
        adapter_factory=lambda: FakeSDKAdapter(scripts=[_script("ok")]),
    )
    session = OrchestratorSession(
        cwd=tmp_path,
        bus=bus,
        manager=manager,
        adapter=FakeSDKAdapter(scripts=[_script("first reply"), _script("second reply")]),
    )
    await session.start()

    bus.publish(UserMessageToOrchestrator("first"))
    bus.publish(UserMessageToOrchestrator("second"))
    await session.wait_idle()

    reply_texts = [r.text for r in replies]
    assert reply_texts == ["first reply", "second reply"]
