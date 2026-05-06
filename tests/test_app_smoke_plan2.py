import pytest
from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
)

from mod_tui.agents.fake_sdk_adapter import FakeSDKAdapter
from mod_tui.agents.manager import AgentManager
from mod_tui.app import ModTuiApp
from mod_tui.events import EventBus
from mod_tui.orchestrator.session import OrchestratorSession
from mod_tui.widgets.agent_table import AgentTable


def _orchestrator_script() -> list:
    """Orchestrator's response to 'spawn it': call spawn_agent then say done."""
    return [
        AssistantMessage(
            content=[
                TextBlock(text="On it. "),
                ToolUseBlock(
                    id="t1",
                    name="spawn_agent",
                    input={"name": "alpha", "prompt": "say hi"},
                ),
            ],
            model="fake-model",
        ),
        # NOTE: in a real SDK flow the tool result would arrive as a
        # UserMessage(ToolResultBlock). The fake adapter doesn't simulate
        # MCP execution; the test asserts on the manager's state directly,
        # not on a full follow-up turn. The ResultMessage below ends the turn.
        ResultMessage(
            subtype="success",
            duration_ms=1, duration_api_ms=1, is_error=False, num_turns=1,
            session_id="fake", total_cost_usd=0.0,
            usage={"input_tokens": 1, "output_tokens": 1},
            result="On it.",
        ),
    ]


def _child_script() -> list:
    return [
        AssistantMessage(content=[TextBlock(text="hi from alpha")], model="fake-model"),
        ResultMessage(
            subtype="success",
            duration_ms=1, duration_api_ms=1, is_error=False, num_turns=1,
            session_id="fake-child", total_cost_usd=0.0,
            usage={"input_tokens": 1, "output_tokens": 1},
            result="hi from alpha",
        ),
    ]


@pytest.mark.asyncio
async def test_orchestrator_can_spawn_agent_and_table_updates(tmp_path):
    bus = EventBus()
    manager = AgentManager(
        cwd=tmp_path,
        bus=bus,
        adapter_factory=lambda: FakeSDKAdapter(scripts=[_child_script()]),
    )
    orchestrator = OrchestratorSession(
        cwd=tmp_path,
        bus=bus,
        manager=manager,
        adapter=FakeSDKAdapter(scripts=[_orchestrator_script()]),
    )
    app = ModTuiApp(cwd=tmp_path, manager=manager, orchestrator=orchestrator)
    app.event_bus = bus

    async with app.run_test() as pilot:
        await pilot.pause()

        # Drive the orchestrator. Because FakeSDKAdapter doesn't actually
        # execute MCP tool calls (the real SDK does that subprocess-side),
        # we invoke spawn_agent directly here as the orchestrator would.
        from mod_tui.orchestrator.tools import build_orchestrator_tools
        spawn, _list, _read = build_orchestrator_tools(manager)
        await spawn({"name": "alpha", "prompt": "say hi"})
        await pilot.pause()

        # AgentTable picked up the AgentSpawned event.
        from textual.widgets import DataTable
        table = app.query_one(AgentTable).query_one(DataTable)
        assert table.row_count == 1

        # Drive the child to completion.
        agent_id = manager.list_infos()[0].id
        await manager.wait_idle(agent_id)

        # Transcript on disk has the child's assistant output.
        entries = manager.read_transcript(agent_id)
        assert any(e.role == "assistant" and e.text == "hi from alpha" for e in entries)
