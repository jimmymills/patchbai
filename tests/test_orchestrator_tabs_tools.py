import json
import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock
from textual.widgets import TabbedContent, TabPane

from mod_tui.agents.fake_sdk_adapter import FakeSDKAdapter
from mod_tui.agents.manager import AgentManager
from mod_tui.app import ModTuiApp
from mod_tui.events import EventBus
from mod_tui.orchestrator.session import OrchestratorSession
from mod_tui.orchestrator.tabs_tools import (
    add_tab_handler,
    close_tab_handler,
    list_tabs_handler,
    switch_tab_handler,
)


def _ok():
    return [
        AssistantMessage(content=[TextBlock(text="ok")], model="fake-model"),
        ResultMessage(
            subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
            num_turns=1, session_id="fake", total_cost_usd=0.0,
            usage={"input_tokens": 1, "output_tokens": 1}, result="ok",
        ),
    ]


def _build_app(tmp_path):
    bus = EventBus()
    manager = AgentManager(
        cwd=tmp_path, bus=bus,
        adapter_factory=lambda: FakeSDKAdapter(scripts=[_ok()]),
    )
    app = ModTuiApp(cwd=tmp_path, manager=manager, global_dir=tmp_path)
    app.event_bus = bus
    app.orchestrator = OrchestratorSession(
        cwd=tmp_path, bus=bus, manager=manager,
        adapter=FakeSDKAdapter(scripts=[_ok()]),
        apply_layout=app._orchestrator_apply_layout,
        layouts_store=app.layouts_store,
        config_store=app.config_store,
        actions=app.actions_registry,
        rebind_keys=app._rebind_keys,
    )
    return app


@pytest.mark.asyncio
async def test_add_tab_with_default_layout(tmp_path):
    app = _build_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        handler = add_tab_handler(app)
        result = await handler({"title": "Logs"})
        await pilot.pause()
        body = json.loads(result["content"][0]["text"])
        assert body["title"] == "Logs"
        assert "tab_id" in body
        # Default seed when workspace already has chat: ActivityFeed-only.
        assert app._workspace is not None
        new_tab = next(t for t in app._workspace.tabs if t.id == body["tab_id"])
        assert new_tab.layout.layout.widget == "ActivityFeed"
        # Activated by default.
        assert app._active_tab_id == body["tab_id"]
        tc = app.query_one("#app-tabs", TabbedContent)
        assert any(p.id == f"tab-{body['tab_id']}" for p in tc.query(TabPane))


@pytest.mark.asyncio
async def test_add_tab_with_inline_layout(tmp_path):
    app = _build_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        handler = add_tab_handler(app)
        layout = {
            "version": 1,
            "layout": {
                "type": "horizontal",
                "children": [
                    {"id": "tree", "widget": "FileTree", "props": {"path": "."}},
                    {"id": "view", "widget": "FileViewer",
                     "props": {"follow_selection": True}},
                ],
            },
        }
        result = await handler({"title": "Code", "layout": layout})
        await pilot.pause()
        body = json.loads(result["content"][0]["text"])
        assert app._workspace is not None
        new_tab = next(t for t in app._workspace.tabs if t.id == body["tab_id"])
        # Container with two children
        assert new_tab.layout.layout.children[0].widget == "FileTree"


@pytest.mark.asyncio
async def test_add_tab_with_named_layout_resolves(tmp_path):
    app = _build_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Save a named layout, then ask add_tab to seed from it by name.
        from mod_tui.layout.spec import LayoutSpec
        named = LayoutSpec.model_validate({
            "version": 1,
            "layout": {"id": "feed", "widget": "ActivityFeed"},
        })
        app.layouts_store.save("monitoring", named)
        handler = add_tab_handler(app)
        result = await handler({"title": "Monitoring", "layout": "monitoring"})
        await pilot.pause()
        body = json.loads(result["content"][0]["text"])
        assert app._workspace is not None
        new_tab = next(t for t in app._workspace.tabs if t.id == body["tab_id"])
        assert new_tab.layout.layout.widget == "ActivityFeed"


@pytest.mark.asyncio
async def test_add_tab_does_not_activate_when_activate_false(tmp_path):
    app = _build_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        original_active = app._active_tab_id
        handler = add_tab_handler(app)
        await handler({"title": "Background", "activate": False})
        await pilot.pause()
        assert app._active_tab_id == original_active


@pytest.mark.asyncio
async def test_add_tab_publishes_tab_added_event(tmp_path):
    app = _build_app(tmp_path)
    seen: list = []
    from mod_tui.events import TabAdded
    app.event_bus.subscribe(TabAdded, lambda e: seen.append(e))
    async with app.run_test() as pilot:
        await pilot.pause()
        handler = add_tab_handler(app)
        await handler({"title": "Logs"})
        await pilot.pause()
    assert any(e.title == "Logs" for e in seen)
