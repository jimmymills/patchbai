"""Regression: _mount_workspace must honor the saved ws.active even when
other tabs' layouts have a `focus` directive or contain widgets that
auto-focus on mount (Input, etc.).

Pre-fix bug: a workspace whose third tab held a Terminal with `focus="term"`
came up on the Terminal tab regardless of which tab the user had saved
as active. Calling .focus() on a panel inside a non-active TabPane causes
Textual to swap the displayed pane to the one containing it.
"""

import json

import pytest
from textual.widgets import TabbedContent

from patchfeld.agents.fake_sdk_adapter import FakeSDKAdapter
from patchfeld.agents.manager import AgentManager
from patchfeld.app import PatchfeldApp
from patchfeld.events import EventBus
from patchfeld.orchestrator.session import OrchestratorSession
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock


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
async def test_mount_honors_saved_active_when_other_tab_has_focus_directive(tmp_path):
    """Saved active="middle" must remain active after mount even though the
    third tab has focus="term" (which would .focus() the Terminal panel
    during apply_layout and pull activation to the third tab pre-fix)."""
    seed = {
        "version": 1,
        "tabs": [
            {
                "id": "first",
                "title": "Agents",
                "layout": {
                    "version": 1,
                    "layout": {
                        "type": "horizontal",
                        "children": [
                            {"id": "orch", "size": "60%", "widget": "OrchestratorChat"},
                            {
                                "type": "tabs", "size": "40%",
                                "children": [
                                    {"id": "agents", "widget": "AgentTable"},
                                    {"id": "feed", "widget": "ActivityFeed"},
                                ],
                                "active": "agents",
                            },
                        ],
                    },
                    "focus": "orch",
                },
            },
            {
                "id": "middle",
                "title": "Files",
                "layout": {
                    "version": 1,
                    "layout": {"id": "feed", "widget": "ActivityFeed"},
                },
            },
            {
                "id": "last",
                "title": "Terminal",
                "layout": {
                    "version": 1,
                    "layout": {"id": "term", "widget": "Terminal"},
                    "focus": "term",
                },
            },
        ],
        "active": "middle",
    }
    (tmp_path / ".patchfeld").mkdir()
    (tmp_path / ".patchfeld" / "workspace.json").write_text(json.dumps(seed))

    app = _build_app(tmp_path)
    async with app.run_test() as pilot:
        # Two pauses: the first lets _mount_workspace finish; the second
        # lets call_after_refresh fire the deferred re-pin.
        await pilot.pause()
        await pilot.pause()

        tc = app.query_one("#app-tabs", TabbedContent)
        assert tc.active == "tab-middle"
        assert app._active_tab_id == "middle"

        # And the persisted workspace shouldn't have been mutated to a
        # different active by mount-time activation churn.
        ws_raw = json.loads((tmp_path / ".patchfeld" / "workspace.json").read_text())
        assert ws_raw["active"] == "middle"
