import pytest
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
    UserMessage,
)

from patchfeld.agents.fake_sdk_adapter import FakeSDKAdapter


def _hello_response() -> list:
    return [
        AssistantMessage(content=[TextBlock(text="hello back")], model="fake-model"),
        ResultMessage(
            subtype="success",
            duration_ms=10,
            duration_api_ms=5,
            is_error=False,
            num_turns=1,
            session_id="fake",
            total_cost_usd=0.001,
            usage={"input_tokens": 5, "output_tokens": 3},
            result="hello back",
        ),
    ]


@pytest.mark.asyncio
async def test_fake_replays_scripted_messages_for_each_query():
    fake = FakeSDKAdapter(scripts=[_hello_response()])
    await fake.start(options=ClaudeAgentOptions())
    await fake.query("hi")
    msgs = [m async for m in fake.stream()]
    assert len(msgs) == 2
    assert isinstance(msgs[0], AssistantMessage)
    assert isinstance(msgs[1], ResultMessage)
    await fake.stop()


@pytest.mark.asyncio
async def test_fake_advances_through_multiple_scripts():
    fake = FakeSDKAdapter(scripts=[_hello_response(), _hello_response()])
    await fake.start(options=ClaudeAgentOptions())

    await fake.query("first")
    msgs1 = [m async for m in fake.stream()]
    await fake.query("second")
    msgs2 = [m async for m in fake.stream()]

    assert len(msgs1) == 2 and len(msgs2) == 2
    await fake.stop()


@pytest.mark.asyncio
async def test_fake_query_without_remaining_scripts_raises():
    fake = FakeSDKAdapter(scripts=[_hello_response()])
    await fake.start(options=ClaudeAgentOptions())
    await fake.query("first")
    [_ async for _ in fake.stream()]

    with pytest.raises(IndexError):
        await fake.query("no script for this")
