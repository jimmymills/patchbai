import json
from pathlib import Path

import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

from mod_tui.agents.fake_sdk_adapter import FakeSDKAdapter
from mod_tui.agents.manager import AgentManager
from mod_tui.events import EventBus
from mod_tui.orchestrator.tools import build_orchestrator_tools


def _ok_script() -> list:
    return [
        AssistantMessage(content=[TextBlock(text="done")], model="fake-model"),
        ResultMessage(
            subtype="success",
            duration_ms=10,
            duration_api_ms=5,
            is_error=False,
            num_turns=1,
            session_id="fake",
            total_cost_usd=0.0,
            usage={"input_tokens": 1, "output_tokens": 1},
            result="done",
        ),
    ]


def _make_manager(tmp_path: Path) -> AgentManager:
    return AgentManager(
        cwd=tmp_path,
        bus=EventBus(),
        adapter_factory=lambda: FakeSDKAdapter(scripts=[_ok_script()]),
    )


@pytest.mark.asyncio
async def test_spawn_agent_tool_creates_agent_and_returns_id(tmp_path: Path):
    manager = _make_manager(tmp_path)
    spawn, _list, _read = build_orchestrator_tools(manager)

    result = await spawn({"name": "research", "prompt": "do the thing"})

    text = result["content"][0]["text"]
    assert "Spawned" in text
    assert len(manager.list_infos()) == 1
    assert manager.list_infos()[0].name == "research"


@pytest.mark.asyncio
async def test_list_agents_tool_returns_json(tmp_path: Path):
    manager = _make_manager(tmp_path)
    spawn, list_tool, _read = build_orchestrator_tools(manager)
    await spawn({"name": "alpha", "prompt": "hi"})

    out = await list_tool({})
    text = out["content"][0]["text"]
    parsed = json.loads(text)
    assert isinstance(parsed, list) and len(parsed) == 1
    assert parsed[0]["name"] == "alpha"
    assert "id" in parsed[0] and "state" in parsed[0]


@pytest.mark.asyncio
async def test_read_agent_transcript_tool_returns_messages(tmp_path: Path):
    manager = _make_manager(tmp_path)
    spawn, _list, read = build_orchestrator_tools(manager)
    spawn_out = await spawn({"name": "alpha", "prompt": "say hi"})
    agent_id = manager.list_infos()[0].id
    await manager.wait_idle(agent_id)

    out = await read({"agent_id": agent_id})
    text = out["content"][0]["text"]
    assert "say hi" in text
    assert "done" in text
