from pathlib import Path

import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

from patchfeld.agents.fake_sdk_adapter import FakeSDKAdapter
from patchfeld.agents.manager import AgentManager
from patchfeld.app import PatchfeldApp
from patchfeld.events import EventBus
from patchfeld.orchestrator.session import OrchestratorSession
from patchfeld.orchestrator.tools import build_orchestrator_tools


def _ok():
    return [
        AssistantMessage(content=[TextBlock(text="ok")], model="fake-model"),
        ResultMessage(
            subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
            num_turns=1, session_id="fake", total_cost_usd=0.0,
            usage={"input_tokens": 1, "output_tokens": 1}, result="ok",
        ),
    ]


@pytest.mark.asyncio
async def test_change_cwd_mcp_tool_routes_to_app(tmp_path):
    proj_a = tmp_path / "a"
    proj_b = tmp_path / "b"
    proj_a.mkdir()
    proj_b.mkdir()
    bus = EventBus()
    manager = AgentManager(
        cwd=proj_a, bus=bus,
        adapter_factory=lambda: FakeSDKAdapter(scripts=[_ok()]),
    )
    app = PatchfeldApp(cwd=proj_a, manager=manager, global_dir=proj_a / ".g")
    app.event_bus = bus
    app.orchestrator = OrchestratorSession(
        cwd=proj_a, bus=bus, manager=manager,
        adapter=FakeSDKAdapter(scripts=[_ok()]),
        apply_layout=app._orchestrator_apply_layout,
        layouts_store=app.layouts_store,
        themes_store=app.themes_store,
        config_store=app.config_store,
        actions=app.actions_registry,
        rebind_keys=app._rebind_keys,
        widget_registry=app.registry,
        current_layout=lambda: app._active_layout(),
        app=app,
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        app.orchestrator._next_adapter_factory = (
            lambda: FakeSDKAdapter(scripts=[_ok()])
        )
        handlers = build_orchestrator_tools(
            manager, apply_layout=app._orchestrator_apply_layout,
            layouts_store=app.layouts_store, themes_store=app.themes_store,
            config_store=app.config_store, actions=app.actions_registry,
            rebind_keys=app._rebind_keys, widget_registry=app.registry,
            current_layout=lambda: app._active_layout(), app=app,
        )
        result = await handlers["change_cwd"]({"path": str(proj_b)})
        await pilot.pause()
        assert "Re-rooted" in result["content"][0]["text"]
        assert app.cwd == proj_b.resolve()
