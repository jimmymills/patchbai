import json

import pytest

from mod_tui.agents.fake_sdk_adapter import FakeSDKAdapter
from mod_tui.agents.manager import AgentManager
from mod_tui.events import EventBus
from mod_tui.layout.defaults import dashboard_layout
from mod_tui.layout.registry import WidgetRegistry
from mod_tui.layout.spec import LayoutSpec
from mod_tui.orchestrator.tools import build_orchestrator_tools
from mod_tui.persistence.layouts_store import NamedLayoutsStore
from mod_tui.widgets.agent_table import AgentTable
from mod_tui.widgets.orchestrator_chat import OrchestratorChat
from mod_tui.widgets.placeholders import ActivityFeed


def _make_manager(tmp_path, ok_script):
    return AgentManager(
        cwd=tmp_path,
        bus=EventBus(),
        adapter_factory=lambda: FakeSDKAdapter(scripts=[ok_script()]),
    )


def _registry() -> WidgetRegistry:
    reg = WidgetRegistry()
    reg.register("OrchestratorChat", OrchestratorChat)
    reg.register("AgentTable", AgentTable)
    reg.register("ActivityFeed", ActivityFeed)
    return reg


@pytest.mark.asyncio
async def test_get_layout_returns_message_when_no_layout_applied(tmp_path, ok_script):
    manager = _make_manager(tmp_path, ok_script)
    store = NamedLayoutsStore(global_dir=tmp_path)

    async def apply_callable(spec, *, layout_name=None):
        pass

    tools = build_orchestrator_tools(
        manager,
        apply_layout=apply_callable,
        layouts_store=store,
        widget_registry=_registry(),
        current_layout=lambda: None,
    )
    out = await tools["get_layout"]({})
    assert "no layout applied" in out["content"][0]["text"].lower()


@pytest.mark.asyncio
async def test_get_layout_returns_dashboard_with_effective_titles(tmp_path, ok_script):
    manager = _make_manager(tmp_path, ok_script)
    store = NamedLayoutsStore(global_dir=tmp_path)
    spec = dashboard_layout()

    async def apply_callable(s, *, layout_name=None):
        pass

    tools = build_orchestrator_tools(
        manager,
        apply_layout=apply_callable,
        layouts_store=store,
        widget_registry=_registry(),
        current_layout=lambda: spec,
    )
    out = await tools["get_layout"]({})
    payload = json.loads(out["content"][0]["text"])
    # Walk the dumped tree and collect (id, title) pairs.
    titles: dict[str, str] = {}

    def _walk(node):
        if "widget" in node:
            titles[node["id"]] = node["title"]
            return
        for c in node["children"]:
            _walk(c)

    _walk(payload["layout"])
    assert titles == {
        "orch": "Orchestrator",
        "agents": "Agents",
        "feed": "Activity",
    }


@pytest.mark.asyncio
async def test_get_layout_preserves_explicit_panel_title(tmp_path, ok_script):
    manager = _make_manager(tmp_path, ok_script)
    store = NamedLayoutsStore(global_dir=tmp_path)
    spec = LayoutSpec.model_validate({
        "version": 1,
        "layout": {
            "type": "horizontal",
            "children": [
                {"id": "orch", "widget": "OrchestratorChat", "title": "My Boss"},
                {"id": "feed", "widget": "ActivityFeed"},
            ],
        },
    })

    async def apply_callable(s, *, layout_name=None):
        pass

    tools = build_orchestrator_tools(
        manager,
        apply_layout=apply_callable,
        layouts_store=store,
        widget_registry=_registry(),
        current_layout=lambda: spec,
    )
    out = await tools["get_layout"]({})
    payload = json.loads(out["content"][0]["text"])
    titles = {}

    def _walk(node):
        if "widget" in node:
            titles[node["id"]] = node["title"]
            return
        for c in node["children"]:
            _walk(c)

    _walk(payload["layout"])
    assert titles["orch"] == "My Boss"
    assert titles["feed"] == "Activity"
