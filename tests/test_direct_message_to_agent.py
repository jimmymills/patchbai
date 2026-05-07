import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

from patchbai.agents.fake_sdk_adapter import FakeSDKAdapter
from patchbai.agents.manager import AgentManager
from patchbai.events import DirectMessageToAgent, EventBus


def _ok():
    return [
        AssistantMessage(content=[TextBlock(text="ack")], model="fake-model"),
        ResultMessage(
            subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
            num_turns=1, session_id="fake", total_cost_usd=0.0,
            usage={"input_tokens": 1, "output_tokens": 1}, result="ack",
        ),
    ]


@pytest.mark.asyncio
async def test_direct_message_event_routes_to_session_send(tmp_path):
    bus = EventBus()
    manager = AgentManager(
        cwd=tmp_path,
        bus=bus,
        adapter_factory=lambda: FakeSDKAdapter(scripts=[_ok(), _ok()]),
    )
    aid = await manager.spawn(name="alpha", prompt="initial")
    await manager.wait_idle(aid)

    bus.publish(DirectMessageToAgent(agent_id=aid, text="from user"))
    await manager.wait_idle(aid)

    entries = manager.read_transcript(aid)
    user_texts = [e.text for e in entries if e.role == "user"]
    assert user_texts == ["initial", "from user"]


@pytest.mark.asyncio
async def test_direct_message_to_unknown_agent_is_silently_ignored(tmp_path):
    bus = EventBus()
    manager = AgentManager(
        cwd=tmp_path,
        bus=bus,
        adapter_factory=lambda: FakeSDKAdapter(scripts=[_ok()]),
    )
    # No spawn — agent_id doesn't exist.
    bus.publish(DirectMessageToAgent(agent_id="ghost", text="hi"))
    # Nothing should raise; nothing else to assert.
