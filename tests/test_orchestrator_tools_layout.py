import json
from pathlib import Path

import pytest

from mod_tui.agents.fake_sdk_adapter import FakeSDKAdapter
from mod_tui.agents.manager import AgentManager
from mod_tui.events import EventBus
from mod_tui.layout.defaults import dashboard_layout
from mod_tui.layout.spec import LayoutSpec
from mod_tui.orchestrator.tools import build_orchestrator_tools
from mod_tui.persistence.layouts_store import NamedLayoutsStore


def _make_manager(tmp_path, ok_script):
    return AgentManager(
        cwd=tmp_path,
        bus=EventBus(),
        adapter_factory=lambda: FakeSDKAdapter(scripts=[ok_script()]),
    )


@pytest.mark.asyncio
async def test_set_layout_calls_the_apply_callable(tmp_path, ok_script):
    manager = _make_manager(tmp_path, ok_script)
    store = NamedLayoutsStore(global_dir=tmp_path)
    applied: list[LayoutSpec] = []

    async def apply_callable(spec: LayoutSpec, *, layout_name: str | None = None, tab_id: str | None = None) -> None:
        applied.append(spec)

    tools = build_orchestrator_tools(
        manager, apply_layout=apply_callable, layouts_store=store
    )
    set_layout = tools["set_layout"]

    spec_dict = dashboard_layout().model_dump(mode="json")
    out = await set_layout({"spec": spec_dict})
    assert "applied" in out["content"][0]["text"].lower()
    assert applied == [dashboard_layout()]


@pytest.mark.asyncio
async def test_save_layout_then_load_round_trips(tmp_path, ok_script):
    manager = _make_manager(tmp_path, ok_script)
    store = NamedLayoutsStore(global_dir=tmp_path)

    async def apply_callable(spec, *, layout_name=None, tab_id=None):
        pass

    tools = build_orchestrator_tools(
        manager, apply_layout=apply_callable, layouts_store=store
    )
    save_layout = tools["save_layout"]
    load_layout = tools["load_layout"]
    list_layouts = tools["list_layouts"]

    spec = dashboard_layout()
    out_save = await save_layout({"name": "triage", "spec": spec.model_dump(mode="json")})
    assert "saved" in out_save["content"][0]["text"].lower()
    out_list = await list_layouts({})
    text = out_list["content"][0]["text"]
    assert "triage" in text
    out_load = await load_layout({"name": "triage"})
    assert "loaded" in out_load["content"][0]["text"].lower()


@pytest.mark.asyncio
async def test_set_layout_with_invalid_spec_returns_error_text(tmp_path, ok_script):
    manager = _make_manager(tmp_path, ok_script)
    store = NamedLayoutsStore(global_dir=tmp_path)

    async def apply_callable(spec, *, layout_name=None, tab_id=None):
        pass

    tools = build_orchestrator_tools(
        manager, apply_layout=apply_callable, layouts_store=store
    )
    set_layout = tools["set_layout"]

    # Two OrchestratorChat panels — violates the at-most-one invariant.
    bad = {
        "version": 1,
        "layout": {
            "type": "horizontal",
            "children": [
                {"id": "a", "widget": "OrchestratorChat"},
                {"id": "b", "widget": "OrchestratorChat"},
            ],
        },
    }
    out = await set_layout({"spec": bad})
    assert "error" in out["content"][0]["text"].lower() or "invalid" in out["content"][0]["text"].lower()


@pytest.mark.asyncio
async def test_load_layout_missing_returns_error_text(tmp_path, ok_script):
    manager = _make_manager(tmp_path, ok_script)
    store = NamedLayoutsStore(global_dir=tmp_path)

    async def apply_callable(spec, *, layout_name=None, tab_id=None):
        pass

    tools = build_orchestrator_tools(
        manager, apply_layout=apply_callable, layouts_store=store
    )
    load_layout = tools["load_layout"]

    out = await load_layout({"name": "nonexistent"})
    text = out["content"][0]["text"].lower()
    assert "not found" in text or "no such layout" in text or "unknown" in text


# ---------------------------------------------------------------------------
# Task 15: tab-aware layout tools
# ---------------------------------------------------------------------------

from mod_tui.app import ModTuiApp
from mod_tui.orchestrator.session import OrchestratorSession
from mod_tui.orchestrator.tabs_tools import add_tab_handler


def _ok():
    from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock
    return [
        AssistantMessage(content=[TextBlock(text="ok")], model="fake-model"),
        ResultMessage(
            subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
            num_turns=1, session_id="fake", total_cost_usd=0.0,
            usage={"input_tokens": 1, "output_tokens": 1}, result="ok",
        ),
    ]


def _build(tmp_path):
    bus = EventBus()
    manager = AgentManager(cwd=tmp_path, bus=bus,
                           adapter_factory=lambda: FakeSDKAdapter(scripts=[_ok()]))
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
        app=app,
    )
    return app


@pytest.mark.asyncio
async def test_set_layout_targets_active_tab_by_default(tmp_path):
    app = _build(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        tools = build_orchestrator_tools(
            app.manager,
            apply_layout=app._orchestrator_apply_layout,
            layouts_store=app.layouts_store,
            widget_registry=app.registry,
            current_layout=lambda: app._active_layout(),
            app=app,
        )
        new_layout = {
            "version": 1,
            "layout": {
                "type": "horizontal",
                "children": [
                    {"id": "orch", "widget": "OrchestratorChat"},
                    {"id": "feed", "widget": "ActivityFeed"},
                ],
            },
        }
        result = await tools["set_layout"]({"spec": new_layout})
        await pilot.pause()
        assert app._workspace is not None
        active_tab = next(t for t in app._workspace.tabs if t.id == app._active_tab_id)
        assert active_tab.layout.layout.children[1].widget == "ActivityFeed"


@pytest.mark.asyncio
async def test_set_layout_with_tab_id_targets_specific_tab(tmp_path):
    app = _build(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        add = add_tab_handler(app)
        r = await add({"title": "Logs", "activate": False})
        new_id = json.loads(r["content"][0]["text"])["tab_id"]
        await pilot.pause()
        original_active = app._active_tab_id

        tools = build_orchestrator_tools(
            app.manager,
            apply_layout=app._orchestrator_apply_layout,
            layouts_store=app.layouts_store,
            widget_registry=app.registry,
            current_layout=lambda: app._active_layout(),
            app=app,
        )
        new_layout = {
            "version": 1,
            "layout": {"id": "tail", "widget": "LogTail",
                       "props": {"file_path": "/tmp/x.log"}},
        }
        await tools["set_layout"]({"spec": new_layout, "tab_id": new_id})
        await pilot.pause()
        assert app._workspace is not None
        target = next(t for t in app._workspace.tabs if t.id == new_id)
        assert target.layout.layout.widget == "LogTail"
        # Active didn't change.
        assert app._active_tab_id == original_active


@pytest.mark.asyncio
async def test_get_layout_includes_tab_metadata(tmp_path):
    app = _build(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        tools = build_orchestrator_tools(
            app.manager,
            apply_layout=app._orchestrator_apply_layout,
            layouts_store=app.layouts_store,
            widget_registry=app.registry,
            current_layout=lambda: app._active_layout(),
            app=app,
        )
        result = await tools["get_layout"]({})
        body = json.loads(result["content"][0]["text"])
        assert body["tab_id"] == app._active_tab_id
        assert "tab_title" in body
        assert "spec" in body  # the LayoutSpec dump


@pytest.mark.asyncio
async def test_load_layout_as_new_tab_creates_a_tab(tmp_path):
    app = _build(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        from mod_tui.layout.spec import LayoutSpec
        named = LayoutSpec.model_validate({
            "version": 1, "layout": {"id": "feed", "widget": "ActivityFeed"},
        })
        app.layouts_store.save("monitoring", named)

        tools = build_orchestrator_tools(
            app.manager,
            apply_layout=app._orchestrator_apply_layout,
            layouts_store=app.layouts_store,
            widget_registry=app.registry,
            current_layout=lambda: app._active_layout(),
            app=app,
        )
        assert app._workspace is not None
        before = len(app._workspace.tabs)
        result = await tools["load_layout"]({"name": "monitoring", "as_new_tab": True})
        await pilot.pause()
        body_text = result["content"][0]["text"]
        assert len(app._workspace.tabs) == before + 1
        assert "monitoring" in body_text
