import json

import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock
from textual.containers import Container as TxContainer
from textual.widgets import TabbedContent, TabPane

from mod_tui.agents.fake_sdk_adapter import FakeSDKAdapter
from mod_tui.agents.manager import AgentManager
from mod_tui.app import ModTuiApp
from mod_tui.events import EventBus
from mod_tui.orchestrator.session import OrchestratorSession


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
async def test_app_starts_with_one_tab_when_no_workspace_exists(tmp_path):
    app = _build_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        tc = app.query_one("#app-tabs", TabbedContent)
        panes = tc.query(TabPane)
        assert len(panes) == 1
        # The default tab's panel-area container is seeded with id "default".
        area = app.query_one("#panel-area-default", TxContainer)
        assert area.id is not None and area.id.startswith("panel-area-")


@pytest.mark.asyncio
async def test_app_seeds_dashboard_layout_on_first_run(tmp_path):
    app = _build_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.query_one("#panel-orch") is not None


@pytest.mark.asyncio
async def test_app_writes_workspace_json_on_launch(tmp_path):
    app = _build_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        ws_path = tmp_path / ".mod_tui" / "workspace.json"
        assert ws_path.exists()


@pytest.mark.asyncio
async def test_legacy_layout_json_is_migrated_to_workspace(tmp_path):
    legacy = {
        "version": 1,
        "layout": {
            "type": "horizontal",
            "children": [
                {"id": "orch", "widget": "OrchestratorChat", "size": "70%"},
                {"id": "feed", "widget": "ActivityFeed", "size": "30%"},
            ],
        },
        "focus": "orch",
    }
    (tmp_path / ".mod_tui").mkdir()
    (tmp_path / ".mod_tui" / "layout.json").write_text(json.dumps(legacy))

    app = _build_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        ws_raw = json.loads((tmp_path / ".mod_tui" / "workspace.json").read_text())
        assert len(ws_raw["tabs"]) == 1
        assert ws_raw["active"] == "default"
        assert ws_raw["tabs"][0]["layout"]["focus"] == "orch"
        assert (tmp_path / ".mod_tui" / "layout.json").exists()


@pytest.mark.asyncio
async def test_tab_activation_updates_workspace_active(tmp_path):
    seed = {
        "version": 1,
        "tabs": [
            {
                "id": "main",
                "title": "Main",
                "layout": {
                    "version": 1,
                    "layout": {"id": "orch", "widget": "OrchestratorChat"},
                },
            },
            {
                "id": "logs",
                "title": "Logs",
                "layout": {
                    "version": 1,
                    "layout": {"id": "feed", "widget": "ActivityFeed"},
                },
            },
        ],
        "active": "main",
    }
    (tmp_path / ".mod_tui").mkdir()
    (tmp_path / ".mod_tui" / "workspace.json").write_text(json.dumps(seed))

    app = _build_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        tc = app.query_one("#app-tabs", TabbedContent)
        tc.active = "tab-logs"
        await pilot.pause()
        assert app._active_tab_id == "logs"
        ws_raw = json.loads((tmp_path / ".mod_tui" / "workspace.json").read_text())
        assert ws_raw["active"] == "logs"


@pytest.mark.asyncio
async def test_tab_activation_publishes_tab_switched_event(tmp_path):
    seed = {
        "version": 1,
        "tabs": [
            {"id": "main", "title": "Main",
             "layout": {"version": 1, "layout": {"id": "orch", "widget": "OrchestratorChat"}}},
            {"id": "logs", "title": "Logs",
             "layout": {"version": 1, "layout": {"id": "feed", "widget": "ActivityFeed"}}},
        ],
        "active": "main",
    }
    (tmp_path / ".mod_tui").mkdir()
    (tmp_path / ".mod_tui" / "workspace.json").write_text(json.dumps(seed))

    app = _build_app(tmp_path)
    seen: list = []
    from mod_tui.events import TabSwitched
    app.event_bus.subscribe(TabSwitched, lambda e: seen.append(e))

    async with app.run_test() as pilot:
        await pilot.pause()
        tc = app.query_one("#app-tabs", TabbedContent)
        tc.active = "tab-logs"
        await pilot.pause()

    assert any(e.tab_id == "logs" and e.title == "Logs" for e in seen)


@pytest.mark.asyncio
async def test_tab_widgets_persist_across_switches(tmp_path):
    """Stateful widgets (e.g., a Notebook scratch buffer) survive switches."""
    seed = {
        "version": 1,
        "tabs": [
            {"id": "main", "title": "Main",
             "layout": {"version": 1, "layout": {"id": "orch", "widget": "OrchestratorChat"}}},
            {"id": "scratch", "title": "Scratch",
             "layout": {
                 "version": 1,
                 "layout": {
                     "id": "note", "widget": "Notebook",
                     "props": {"name": "memo"},
                 },
             }},
        ],
        "active": "main",
    }
    (tmp_path / ".mod_tui").mkdir()
    (tmp_path / ".mod_tui" / "workspace.json").write_text(json.dumps(seed))

    app = _build_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        notebook = app.query_one("#panel-note")
        assert notebook is not None
        tc = app.query_one("#app-tabs", TabbedContent)
        tc.active = "tab-scratch"
        await pilot.pause()
        same = app.query_one("#panel-note")
        assert same is notebook
        tc.active = "tab-main"
        await pilot.pause()
        assert app.query_one("#panel-note") is notebook
