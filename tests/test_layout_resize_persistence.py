import json
from pathlib import Path

import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

from mod_tui.agents.fake_sdk_adapter import FakeSDKAdapter
from mod_tui.agents.manager import AgentManager
from mod_tui.app import (
    ModTuiApp,
    _apply_resize,
    _cells_to_percentages,
    _normalize_layout_percentages,
)
from mod_tui.events import EventBus, LayoutResized
from mod_tui.orchestrator.session import OrchestratorSession
from mod_tui.persistence.paths import project_workspace_path


def _ok_script() -> list:
    return [
        AssistantMessage(content=[TextBlock(text="ack")], model="fake-model"),
        ResultMessage(
            subtype="success",
            duration_ms=1, duration_api_ms=1, is_error=False, num_turns=1,
            session_id="fake", total_cost_usd=0.0,
            usage={"input_tokens": 1, "output_tokens": 1},
            result="ack",
        ),
    ]


def _build_test_app(tmp_path: Path) -> ModTuiApp:
    bus = EventBus()
    manager = AgentManager(
        cwd=tmp_path,
        bus=bus,
        adapter_factory=lambda: FakeSDKAdapter(scripts=[_ok_script()]),
    )
    orch = OrchestratorSession(
        cwd=tmp_path,
        bus=bus,
        manager=manager,
        adapter=FakeSDKAdapter(scripts=[_ok_script()]),
    )
    app = ModTuiApp(cwd=tmp_path, manager=manager, orchestrator=orch)
    app.event_bus = bus
    return app


# --- _cells_to_percentages -------------------------------------------------

def test_cells_to_percentages_sums_to_100():
    pcts = _cells_to_percentages((60, 40))
    assert pcts == ["60%", "40%"]
    pcts = _cells_to_percentages((30, 30, 40))
    assert pcts == ["30%", "30%", "40%"]


def test_cells_to_percentages_handles_rounding_drift():
    # 33.33 + 33.33 + 33.33 → naively rounds to 33+33+33 = 99; the helper
    # absorbs the remainder into the last entry.
    pcts = _cells_to_percentages((1, 1, 1))
    assert pcts == ["33%", "33%", "34%"]
    assert sum(int(p[:-1]) for p in pcts) == 100


def test_cells_to_percentages_minimum_one_percent():
    # A tiny child shouldn't disappear to 0%.
    pcts = _cells_to_percentages((100, 1))
    assert pcts[1] == "1%"
    assert sum(int(p[:-1]) for p in pcts) == 100


# --- _apply_resize ---------------------------------------------------------

def test_apply_resize_writes_normalized_percentages():
    layout = {
        "type": "horizontal",
        "children": [
            {"id": "a", "widget": "OrchestratorChat", "size": "60%"},
            {"id": "b", "widget": "AgentTable", "size": "40%"},
        ],
    }
    ok = _apply_resize(layout, parent_path=(), children_cells=(120, 80))
    assert ok is True
    assert layout["children"][0]["size"] == "60%"
    assert layout["children"][1]["size"] == "40%"
    # Percentages always sum to 100% — the bug fix.
    sizes = [c["size"] for c in layout["children"]]
    assert sum(int(s[:-1]) for s in sizes) == 100


def test_apply_resize_nested_container():
    layout = {
        "type": "horizontal",
        "children": [
            {"id": "orch", "widget": "OrchestratorChat", "size": "60%"},
            {
                "type": "vertical",
                "size": "40%",
                "children": [
                    {"id": "a", "widget": "AgentTable", "size": "50%"},
                    {"id": "f", "widget": "ActivityFeed", "size": "50%"},
                ],
            },
        ],
    }
    ok = _apply_resize(layout, parent_path=(1,), children_cells=(70, 30))
    assert ok is True
    inner = layout["children"][1]["children"]
    assert inner[0]["size"] == "70%"
    assert inner[1]["size"] == "30%"


def test_apply_resize_rejects_invalid_path():
    layout = {"type": "horizontal", "children": [
        {"id": "a", "widget": "OrchestratorChat"},
    ]}
    assert _apply_resize(layout, parent_path=(0, 0), children_cells=(100,)) is False
    assert _apply_resize(layout, parent_path=(0,), children_cells=(100,)) is False


def test_apply_resize_rejects_mismatched_child_count():
    layout = {
        "type": "horizontal",
        "children": [
            {"id": "a", "widget": "OrchestratorChat", "size": "60%"},
            {"id": "b", "widget": "AgentTable", "size": "40%"},
        ],
    }
    # Two children but three cells provided — refuse rather than guess.
    assert _apply_resize(layout, parent_path=(), children_cells=(60, 30, 10)) is False


# --- _normalize_layout_percentages (migration) -----------------------------

def test_normalize_layout_repairs_drifted_percentages():
    layout = {
        "type": "horizontal",
        "children": [
            {"id": "a", "widget": "OrchestratorChat", "size": "52%"},
            {"id": "b", "widget": "AgentTable", "size": "26%"},
        ],
    }
    changed = _normalize_layout_percentages(layout)
    assert changed is True
    sizes = [c["size"] for c in layout["children"]]
    assert sum(int(s[:-1]) for s in sizes) == 100
    # Ratio preserved: 52/26 ≈ 2/1; result should split similarly.
    assert int(layout["children"][0]["size"][:-1]) > int(layout["children"][1]["size"][:-1])


def test_normalize_layout_leaves_already_normalized_alone():
    layout = {
        "type": "horizontal",
        "children": [
            {"id": "a", "widget": "OrchestratorChat", "size": "60%"},
            {"id": "b", "widget": "AgentTable", "size": "40%"},
        ],
    }
    assert _normalize_layout_percentages(layout) is False
    assert layout["children"][0]["size"] == "60%"


def test_normalize_layout_skips_mixed_unit_containers():
    # Mixed percent + None/fr children — don't touch (intent unclear).
    layout = {
        "type": "horizontal",
        "children": [
            {"id": "a", "widget": "OrchestratorChat", "size": "60%"},
            {"id": "b", "widget": "AgentTable", "size": None},
        ],
    }
    assert _normalize_layout_percentages(layout) is False


def test_normalize_layout_recurses_into_nested_containers():
    layout = {
        "type": "horizontal",
        "children": [
            {"id": "a", "widget": "OrchestratorChat", "size": "60%"},
            {
                "type": "vertical",
                "size": "40%",
                "children": [
                    {"id": "b", "widget": "AgentTable", "size": "30%"},
                    {"id": "c", "widget": "ActivityFeed", "size": "30%"},
                ],
            },
        ],
    }
    changed = _normalize_layout_percentages(layout)
    assert changed is True
    inner = layout["children"][1]["children"]
    assert sum(int(c["size"][:-1]) for c in inner) == 100


# --- App-level persistence handler -----------------------------------------

@pytest.mark.asyncio
async def test_layout_resized_event_persists_normalized_sizes(tmp_path: Path):
    app = _build_test_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Drag the root splitter so children outer cells become 60/40 of 100.
        app.event_bus.publish(LayoutResized(
            tab_id="default",
            parent_path=(),
            children_cells=(60, 40),
        ))
        await pilot.pause()

        ws_path = project_workspace_path(tmp_path)
        raw = json.loads(ws_path.read_text())
        children = raw["tabs"][0]["layout"]["layout"]["children"]
        assert children[0]["size"] == "60%"
        assert children[1]["size"] == "40%"
        # Critical invariant: sums to 100% — the previous bug saved 60%+40%
        # as raw inner-cell ratios, dropping a few percent each save.
        assert sum(int(c["size"][:-1]) for c in children) == 100

        assert app._workspace is not None
        root = app._workspace.tabs[0].layout.layout
        assert root.children[0].size == "60%"  # type: ignore[union-attr]
        assert root.children[1].size == "40%"  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_layout_resized_for_unknown_tab_is_a_noop(tmp_path: Path):
    app = _build_test_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        before = app._workspace.tabs[0].layout.layout.children[0].size  # type: ignore[union-attr]
        app.event_bus.publish(LayoutResized(
            tab_id="does-not-exist",
            parent_path=(),
            children_cells=(10, 90),
        ))
        await pilot.pause()
        after = app._workspace.tabs[0].layout.layout.children[0].size  # type: ignore[union-attr]
        assert before == after


@pytest.mark.asyncio
async def test_reset_panel_sizes_restores_named_layout(tmp_path: Path):
    app = _build_test_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.event_bus.publish(LayoutResized(
            tab_id="default",
            parent_path=(),
            children_cells=(20, 80),
        ))
        await pilot.pause()
        assert app._workspace.tabs[0].layout.layout.children[0].size == "20%"  # type: ignore[union-attr]

        app._current_layout_name = "default"
        await app.action_reset_panel_sizes()
        await pilot.pause()

        assert app._workspace.tabs[0].layout.layout.children[0].size == "60%"  # type: ignore[union-attr]


# --- Migration on workspace load -------------------------------------------

@pytest.mark.asyncio
async def test_drifted_workspace_is_repaired_on_app_load(tmp_path: Path):
    """A workspace.json saved by the buggy splitter (sums < 100%) should be
    auto-repaired to sum to 100% on launch and re-persisted."""
    from mod_tui.persistence.paths import project_workspace_path
    drifted = {
        "version": 1,
        "tabs": [
            {
                "id": "default",
                "title": "Agents",
                "layout": {
                    "version": 1,
                    "layout": {
                        "type": "horizontal",
                        "size": None,
                        "children": [
                            {
                                "id": "orch",
                                "widget": "OrchestratorChat",
                                "props": {},
                                "size": "52%",
                                "title": None,
                            },
                            {
                                "id": "agents",
                                "widget": "AgentTable",
                                "props": {},
                                "size": "26%",
                                "title": None,
                            },
                        ],
                    },
                    "focus": None,
                    "custom_widgets": [],
                },
            }
        ],
        "active": "default",
        "active_theme": None,
    }
    ws_path = project_workspace_path(tmp_path)
    ws_path.parent.mkdir(parents=True, exist_ok=True)
    ws_path.write_text(json.dumps(drifted))

    app = _build_test_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        # In-memory workspace was repaired.
        assert app._workspace is not None
        root_children = app._workspace.tabs[0].layout.layout.children  # type: ignore[union-attr]
        sizes = [c.size for c in root_children]
        assert sum(int(s[:-1]) for s in sizes) == 100  # type: ignore[index]
        # Disk was rewritten to the repaired layout.
        repaired_raw = json.loads(ws_path.read_text())
        on_disk = [c["size"] for c in repaired_raw["tabs"][0]["layout"]["layout"]["children"]]
        assert sum(int(s[:-1]) for s in on_disk) == 100
