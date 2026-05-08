import json
import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock
from textual.widgets import TabbedContent, TabPane

from patchfeld.agents.fake_sdk_adapter import FakeSDKAdapter
from patchfeld.agents.manager import AgentManager
from patchfeld.app import PatchfeldApp
from patchfeld.events import EventBus
from patchfeld.orchestrator.session import OrchestratorSession
from patchfeld.orchestrator.tabs_tools import (
    add_tab_handler,
    close_tab_handler,
    list_tabs_handler,
    rename_tab_handler,
    reorder_tabs_handler,
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
    app = PatchfeldApp(cwd=tmp_path, manager=manager, global_dir=tmp_path)
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
        from patchfeld.layout.spec import LayoutSpec
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
    from patchfeld.events import TabAdded
    app.event_bus.subscribe(TabAdded, lambda e: seen.append(e))
    async with app.run_test() as pilot:
        await pilot.pause()
        handler = add_tab_handler(app)
        await handler({"title": "Logs"})
        await pilot.pause()
    assert any(e.title == "Logs" for e in seen)


@pytest.mark.asyncio
async def test_close_tab_removes_pane_and_updates_workspace(tmp_path):
    app = _build_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Add a second tab so close has something to remove.
        add = add_tab_handler(app)
        result = await add({"title": "Logs"})
        new_id = json.loads(result["content"][0]["text"])["tab_id"]
        await pilot.pause()

        close = close_tab_handler(app)
        result = await close({"tab_id": new_id})
        await pilot.pause()
        body = json.loads(result["content"][0]["text"])
        assert body["closed"] == new_id
        assert app._workspace is not None
        assert all(t.id != new_id for t in app._workspace.tabs)
        tc = app.query_one("#app-tabs", TabbedContent)
        assert all(p.id != f"tab-{new_id}" for p in tc.query(TabPane))


@pytest.mark.asyncio
async def test_close_tab_refuses_to_close_last_tab(tmp_path):
    app = _build_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app._workspace is not None
        only_id = app._workspace.tabs[0].id
        close = close_tab_handler(app)
        result = await close({"tab_id": only_id})
        body = json.loads(result["content"][0]["text"])
        assert body["error"] == "would_leave_zero_tabs"
        assert app._workspace is not None
        assert any(t.id == only_id for t in app._workspace.tabs)


@pytest.mark.asyncio
async def test_close_tab_refuses_when_no_chat_remains(tmp_path):
    # Seed a workspace where tab "main" has chat and tab "logs" doesn't.
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
    (tmp_path / ".patchfeld").mkdir()
    (tmp_path / ".patchfeld" / "workspace.json").write_text(json.dumps(seed))
    app = _build_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        close = close_tab_handler(app)
        result = await close({"tab_id": "main"})
        body = json.loads(result["content"][0]["text"])
        assert body["error"] == "would_leave_no_chat"
        assert app._workspace is not None
        assert any(t.id == "main" for t in app._workspace.tabs)


@pytest.mark.asyncio
async def test_close_tab_unknown_id_returns_error(tmp_path):
    app = _build_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        close = close_tab_handler(app)
        result = await close({"tab_id": "ghost"})
        body = json.loads(result["content"][0]["text"])
        assert body["error"] == "unknown_tab_id"


@pytest.mark.asyncio
async def test_close_tab_publishes_tab_closed_event(tmp_path):
    app = _build_app(tmp_path)
    seen: list = []
    from patchfeld.events import TabClosed
    app.event_bus.subscribe(TabClosed, lambda e: seen.append(e))
    async with app.run_test() as pilot:
        await pilot.pause()
        add = add_tab_handler(app)
        result = await add({"title": "Logs"})
        new_id = json.loads(result["content"][0]["text"])["tab_id"]
        await pilot.pause()
        close = close_tab_handler(app)
        await close({"tab_id": new_id})
        await pilot.pause()
    assert any(e.tab_id == new_id for e in seen)


@pytest.mark.asyncio
async def test_close_active_tab_falls_back_to_neighbor(tmp_path):
    app = _build_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        add = add_tab_handler(app)
        r1 = await add({"title": "Logs", "activate": True})
        new_id = json.loads(r1["content"][0]["text"])["tab_id"]
        await pilot.pause()
        assert app._active_tab_id == new_id
        close = close_tab_handler(app)
        await close({"tab_id": new_id})
        await pilot.pause()
        # Active falls back to the previous tab.
        assert app._active_tab_id != new_id
        assert app._active_tab_id is not None


@pytest.mark.asyncio
async def test_switch_tab_changes_active(tmp_path):
    app = _build_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        add = add_tab_handler(app)
        r = await add({"title": "Logs", "activate": False})
        new_id = json.loads(r["content"][0]["text"])["tab_id"]
        await pilot.pause()
        original_active = app._active_tab_id

        switch = switch_tab_handler(app)
        result = await switch({"tab_id": new_id})
        await pilot.pause()
        body = json.loads(result["content"][0]["text"])
        assert body["active"] == new_id
        assert app._active_tab_id == new_id
        assert app._active_tab_id != original_active


@pytest.mark.asyncio
async def test_switch_tab_unknown_id_returns_error(tmp_path):
    app = _build_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        switch = switch_tab_handler(app)
        result = await switch({"tab_id": "ghost"})
        body = json.loads(result["content"][0]["text"])
        assert body["error"] == "unknown_tab_id"


@pytest.mark.asyncio
async def test_list_tabs_returns_metadata(tmp_path):
    app = _build_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        add = add_tab_handler(app)
        await add({"title": "Logs", "activate": False})
        await pilot.pause()
        ls = list_tabs_handler(app)
        result = await ls({})
        body = json.loads(result["content"][0]["text"])
        assert isinstance(body, list)
        assert len(body) == 2
        for entry in body:
            assert {"id", "title", "active", "has_chat", "panel_ids"} <= set(entry.keys())
        assert sum(1 for t in body if t["active"]) == 1


@pytest.mark.asyncio
async def test_list_tabs_panel_ids_include_panels_in_panel_tabs(tmp_path):
    app = _build_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        add = add_tab_handler(app)
        await add({
            "title": "Mixed",
            "layout": {
                "version": 1,
                "layout": {
                    "type": "tabs",
                    "children": [
                        {"id": "feed", "widget": "ActivityFeed"},
                        {"id": "logs", "widget": "LogTail"},
                    ],
                },
            },
            "activate": False,
        })
        await pilot.pause()
        ls = list_tabs_handler(app)
        result = await ls({})
        body = json.loads(result["content"][0]["text"])
        mixed = next(t for t in body if t["title"] == "Mixed")
        assert set(mixed["panel_ids"]) == {"feed", "logs"}


# --- rename_tab ------------------------------------------------------------

@pytest.mark.asyncio
async def test_rename_tab_updates_title_and_strip_label(tmp_path):
    app = _build_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app._workspace is not None
        original_id = app._workspace.tabs[0].id

        rename = rename_tab_handler(app)
        result = await rename({"tab_id": original_id, "title": "Renamed"})
        await pilot.pause()
        body = json.loads(result["content"][0]["text"])
        assert body["renamed"] == original_id
        assert body["title"] == "Renamed"

        # Workspace model reflects the new title.
        assert app._workspace.tabs[0].title == "Renamed"
        # Strip label updated in place — no remount.
        tc = app.query_one("#app-tabs", TabbedContent)
        tab = tc.get_tab(f"tab-{original_id}")
        assert str(tab.label) == "Renamed"


@pytest.mark.asyncio
async def test_rename_tab_unknown_id_returns_error(tmp_path):
    app = _build_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        rename = rename_tab_handler(app)
        result = await rename({"tab_id": "nope", "title": "Whatever"})
        body = json.loads(result["content"][0]["text"])
        assert body.get("error") == "unknown_tab_id"


@pytest.mark.asyncio
async def test_rename_tab_empty_title_returns_error(tmp_path):
    app = _build_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        original_id = app._workspace.tabs[0].id  # type: ignore[union-attr]
        rename = rename_tab_handler(app)
        result = await rename({"tab_id": original_id, "title": "  "})
        body = json.loads(result["content"][0]["text"])
        assert "title" in body.get("error", "")


@pytest.mark.asyncio
async def test_rename_tab_persists_to_disk(tmp_path):
    from patchfeld.persistence.paths import project_workspace_path
    app = _build_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        original_id = app._workspace.tabs[0].id  # type: ignore[union-attr]
        rename = rename_tab_handler(app)
        await rename({"tab_id": original_id, "title": "OnDisk"})
        await pilot.pause()
        raw = json.loads(project_workspace_path(tmp_path).read_text())
        assert raw["tabs"][0]["title"] == "OnDisk"


# --- reorder_tabs ----------------------------------------------------------

@pytest.mark.asyncio
async def test_reorder_tabs_rearranges_workspace_and_strip(tmp_path):
    app = _build_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Add two more tabs so we have three to permute.
        add = add_tab_handler(app)
        r1 = json.loads((await add({"title": "Two", "activate": False}))["content"][0]["text"])
        r2 = json.loads((await add({"title": "Three", "activate": False}))["content"][0]["text"])
        await pilot.pause()
        ids = [t.id for t in app._workspace.tabs]  # type: ignore[union-attr]
        assert len(ids) == 3 and r1["tab_id"] in ids and r2["tab_id"] in ids

        new_order = [ids[2], ids[0], ids[1]]
        reorder = reorder_tabs_handler(app)
        result = await reorder({"tab_ids": new_order})
        await pilot.pause()
        body = json.loads(result["content"][0]["text"])
        assert body["reordered"] == new_order

        # Workspace tabs match the new order.
        assert [t.id for t in app._workspace.tabs] == new_order  # type: ignore[union-attr]

        # The strip's TabPanes appear in the new order. Filter to the
        # app-level tabs (id prefix `tab-`); panel-level Tabs containers
        # nested inside the layout render their own TabPanes (prefix
        # `tabpane-`) which would otherwise pollute this assertion.
        tc = app.query_one("#app-tabs", TabbedContent)
        pane_ids = [p.id for p in tc.query(TabPane) if p.id and p.id.startswith("tab-")]
        assert pane_ids == [f"tab-{tid}" for tid in new_order]


@pytest.mark.asyncio
async def test_reorder_tabs_preserves_active_tab(tmp_path):
    app = _build_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        add = add_tab_handler(app)
        b = json.loads((await add({"title": "B", "activate": False}))["content"][0]["text"])
        await pilot.pause()
        active_before = app._active_tab_id
        ids = [t.id for t in app._workspace.tabs]  # type: ignore[union-attr]
        # Reverse the order.
        new_order = list(reversed(ids))
        reorder = reorder_tabs_handler(app)
        await reorder({"tab_ids": new_order})
        await pilot.pause()
        assert app._active_tab_id == active_before
        assert b["tab_id"] in [t.id for t in app._workspace.tabs]  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_reorder_tabs_rejects_non_permutation(tmp_path):
    app = _build_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        ids = [t.id for t in app._workspace.tabs]  # type: ignore[union-attr]
        reorder = reorder_tabs_handler(app)
        # Missing id
        body = json.loads((await reorder({"tab_ids": []}))["content"][0]["text"])
        assert body.get("error") == "tab_ids_not_a_permutation"
        # Extra id
        body = json.loads((await reorder({"tab_ids": ids + ["bogus"]}))["content"][0]["text"])
        assert body.get("error") == "tab_ids_not_a_permutation"
        # Duplicates
        body = json.loads((await reorder({"tab_ids": [ids[0], ids[0]]}))["content"][0]["text"])
        assert body.get("error") == "tab_ids_not_a_permutation"


@pytest.mark.asyncio
async def test_reorder_tabs_persists_to_disk(tmp_path):
    from patchfeld.persistence.paths import project_workspace_path
    app = _build_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        add = add_tab_handler(app)
        await add({"title": "Two", "activate": False})
        await pilot.pause()
        ids = [t.id for t in app._workspace.tabs]  # type: ignore[union-attr]
        reorder = reorder_tabs_handler(app)
        await reorder({"tab_ids": list(reversed(ids))})
        await pilot.pause()
        raw = json.loads(project_workspace_path(tmp_path).read_text())
        assert [t["id"] for t in raw["tabs"]] == list(reversed(ids))
