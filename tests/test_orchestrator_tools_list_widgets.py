import json

import pytest
from textual.widget import Widget

from mod_tui.agents.fake_sdk_adapter import FakeSDKAdapter
from mod_tui.agents.manager import AgentManager
from mod_tui.events import EventBus
from mod_tui.layout.registry import WidgetRegistry
from mod_tui.orchestrator.tools import build_orchestrator_tools


class _W(Widget):
    pass


@pytest.mark.asyncio
async def test_list_widgets_returns_registry_metadata(tmp_path, ok_script):
    reg = WidgetRegistry()
    reg.register("OrchestratorChat", _W, description="manager chat")
    reg.register("Markdown", _W, description="renders markdown",
                 props_schema={"source": str})

    manager = AgentManager(
        cwd=tmp_path, bus=EventBus(),
        adapter_factory=lambda: FakeSDKAdapter(scripts=[ok_script()]),
    )
    tools = build_orchestrator_tools(manager, widget_registry=reg)
    out = await tools["list_widgets"]({})
    parsed = json.loads(out["content"][0]["text"])
    by_name = {w["name"]: w for w in parsed}
    assert "OrchestratorChat" in by_name
    assert by_name["Markdown"]["description"] == "renders markdown"
    assert by_name["Markdown"]["props_schema"] == {"source": "str"}


@pytest.mark.asyncio
async def test_list_widgets_omitted_when_no_registry_passed(tmp_path, ok_script):
    manager = AgentManager(
        cwd=tmp_path, bus=EventBus(),
        adapter_factory=lambda: FakeSDKAdapter(scripts=[ok_script()]),
    )
    tools = build_orchestrator_tools(manager)
    assert "list_widgets" not in tools
