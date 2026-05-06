import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

from mod_tui.agents.fake_sdk_adapter import FakeSDKAdapter
from mod_tui.agents.manager import AgentManager
from mod_tui.events import EventBus
from mod_tui.orchestrator.tools import build_orchestrator_tools


def _script(text: str) -> list:
    return [
        AssistantMessage(content=[TextBlock(text=text)], model="fake-model"),
        ResultMessage(
            subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
            num_turns=1, session_id="fake", total_cost_usd=0.0,
            usage={"input_tokens": 1, "output_tokens": 1}, result=text,
        ),
    ]


def _make_manager(tmp_path):
    return AgentManager(
        cwd=tmp_path,
        bus=EventBus(),
        adapter_factory=lambda: FakeSDKAdapter(scripts=[_script("first"), _script("second")]),
    )


@pytest.mark.asyncio
async def test_send_to_agent_appends_followup_to_transcript(tmp_path):
    manager = _make_manager(tmp_path)
    tools = build_orchestrator_tools(manager)
    spawn = tools["spawn_agent"]
    send = tools["send_to_agent"]

    await spawn({"name": "alpha", "prompt": "say first"})
    aid = manager.list_infos()[0].id
    await manager.wait_idle(aid)

    await send({"agent_id": aid, "message": "say second"})
    await manager.wait_idle(aid)

    entries = manager.read_transcript(aid)
    user_texts = [e.text for e in entries if e.role == "user"]
    assert user_texts == ["say first", "say second"]


@pytest.mark.asyncio
async def test_kill_agent_removes_session(tmp_path):
    manager = _make_manager(tmp_path)
    tools = build_orchestrator_tools(manager)
    spawn = tools["spawn_agent"]
    kill = tools["kill_agent"]

    await spawn({"name": "alpha", "prompt": "say first"})
    aid = manager.list_infos()[0].id
    await manager.wait_idle(aid)

    out = await kill({"agent_id": aid})
    assert "killed" in out["content"][0]["text"].lower()
    assert manager.get_session(aid) is None


@pytest.mark.asyncio
async def test_interrupt_agent_calls_interrupt(tmp_path):
    manager = _make_manager(tmp_path)
    tools = build_orchestrator_tools(manager)
    spawn = tools["spawn_agent"]
    interrupt = tools["interrupt_agent"]

    await spawn({"name": "alpha", "prompt": "say first"})
    aid = manager.list_infos()[0].id

    out = await interrupt({"agent_id": aid})
    # The fake adapter's interrupt is a no-op, but the tool should at least
    # find the agent and not raise.
    assert "interrupt" in out["content"][0]["text"].lower()
