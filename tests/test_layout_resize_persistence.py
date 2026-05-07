import json
from pathlib import Path

import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

from mod_tui.agents.fake_sdk_adapter import FakeSDKAdapter
from mod_tui.agents.manager import AgentManager
from mod_tui.app import ModTuiApp, _apply_size_updates
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


# --- _apply_size_updates unit tests ----------------------------------------

def test_apply_size_updates_root_container():
    layout = {
        "type": "horizontal",
        "children": [
            {"id": "a", "widget": "OrchestratorChat", "size": "60%"},
            {"id": "b", "widget": "AgentTable", "size": "40%"},
        ],
    }
    ok = _apply_size_updates(layout, parent_path=(), updates=((0, "47%"), (1, "53%")))
    assert ok is True
    assert layout["children"][0]["size"] == "47%"
    assert layout["children"][1]["size"] == "53%"


def test_apply_size_updates_nested_container():
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
    ok = _apply_size_updates(
        layout, parent_path=(1,), updates=((0, "70%"), (1, "30%")),
    )
    assert ok is True
    inner = layout["children"][1]["children"]
    assert inner[0]["size"] == "70%"
    assert inner[1]["size"] == "30%"


def test_apply_size_updates_invalid_path_returns_false():
    layout = {"type": "horizontal", "children": [
        {"id": "a", "widget": "OrchestratorChat"},
    ]}
    # path goes deeper than the tree
    assert _apply_size_updates(layout, parent_path=(0, 0), updates=((0, "50%"),)) is False
    # leaf has no children
    assert _apply_size_updates(layout, parent_path=(0,), updates=((0, "50%"),)) is False


# --- App-level persistence handler -----------------------------------------

@pytest.mark.asyncio
async def test_layout_resized_event_persists_sizes_to_disk(tmp_path: Path):
    app = _build_test_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Default dashboard layout is horizontal: orch (60%) + vertical(40%).
        # Drag the root splitter to 47%/53%.
        app.event_bus.publish(LayoutResized(
            tab_id="default",
            parent_path=(),
            updates=((0, "47%"), (1, "53%")),
        ))
        await pilot.pause()

        ws_path = project_workspace_path(tmp_path)
        raw = json.loads(ws_path.read_text())
        # workspace has one tab; layout root is a horizontal Container with 2 children.
        children = raw["tabs"][0]["layout"]["layout"]["children"]
        assert children[0]["size"] == "47%"
        assert children[1]["size"] == "53%"

        # In-memory workspace tracks the same change.
        assert app._workspace is not None
        root = app._workspace.tabs[0].layout.layout
        assert root.children[0].size == "47%"  # type: ignore[union-attr]
        assert root.children[1].size == "53%"  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_layout_resized_for_unknown_tab_is_a_noop(tmp_path: Path):
    app = _build_test_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        before = app._workspace.tabs[0].layout.layout.children[0].size  # type: ignore[union-attr]
        app.event_bus.publish(LayoutResized(
            tab_id="does-not-exist",
            parent_path=(),
            updates=((0, "10%"), (1, "90%")),
        ))
        await pilot.pause()
        after = app._workspace.tabs[0].layout.layout.children[0].size  # type: ignore[union-attr]
        assert before == after


@pytest.mark.asyncio
async def test_reset_panel_sizes_restores_named_layout(tmp_path: Path):
    app = _build_test_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Simulate a drag that mutated sizes off the dashboard defaults.
        app.event_bus.publish(LayoutResized(
            tab_id="default",
            parent_path=(),
            updates=((0, "20%"), (1, "80%")),
        ))
        await pilot.pause()
        assert app._workspace.tabs[0].layout.layout.children[0].size == "20%"  # type: ignore[union-attr]

        # Pretend the user loaded the canonical "default" layout (which on_mount
        # seeded with dashboard_layout()) so reset has a name to fall back to.
        app._current_layout_name = "default"
        await app.action_reset_panel_sizes()
        await pilot.pause()

        assert app._workspace.tabs[0].layout.layout.children[0].size == "60%"  # type: ignore[union-attr]
