import asyncio

import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

from mod_tui.agents.fake_sdk_adapter import FakeSDKAdapter
from mod_tui.agents.manager import AgentManager
from mod_tui.events import EventBus
from mod_tui.orchestrator.tools import build_orchestrator_tools


def _ok():
    return [
        AssistantMessage(content=[TextBlock(text="done")], model="fake-model"),
        ResultMessage(
            subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
            num_turns=1, session_id="fake", total_cost_usd=0.0,
            usage={"input_tokens": 1, "output_tokens": 1}, result="done",
        ),
    ]


@pytest.mark.asyncio
async def test_respond_to_agent_request_resolves_pending_inbox_entry(tmp_path):
    manager = AgentManager(
        cwd=tmp_path,
        bus=EventBus(),
        adapter_factory=lambda: FakeSDKAdapter(scripts=[_ok()]),
    )
    tools = build_orchestrator_tools(manager)
    spawn = tools["spawn_agent"]
    respond = tools["respond_to_agent_request"]

    await spawn({"name": "alpha", "prompt": "hi"})
    aid = manager.list_infos()[0].id
    await manager.wait_idle(aid)

    inbox = manager.get_inbox(aid)
    request_id = inbox.register()

    waiter = asyncio.create_task(inbox.wait(request_id, timeout_s=1.0))
    out = await respond(
        {"agent_id": aid, "request_id": request_id, "response": "ship it"}
    )
    assert "resolved" in out["content"][0]["text"].lower()

    answer = await waiter
    assert answer == "ship it"


@pytest.mark.asyncio
async def test_respond_to_unknown_agent_returns_error_text(tmp_path):
    manager = AgentManager(
        cwd=tmp_path,
        bus=EventBus(),
        adapter_factory=lambda: FakeSDKAdapter(scripts=[_ok()]),
    )
    tools = build_orchestrator_tools(manager)
    respond = tools["respond_to_agent_request"]

    out = await respond(
        {"agent_id": "nope", "request_id": "x", "response": "anything"}
    )
    assert "unknown" in out["content"][0]["text"].lower() or "no inbox" in out["content"][0]["text"].lower()
