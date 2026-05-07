import pytest
from textual.app import App, ComposeResult
from textual.containers import Container as TxContainer
from textual.widgets import TabbedContent, TabPane

from mod_tui.layout.engine import apply
from mod_tui.layout.registry import WidgetRegistry
from mod_tui.layout.spec import LayoutSpec
from mod_tui.widgets.orchestrator_chat import OrchestratorChat
from mod_tui.widgets.placeholders import ActivityFeed
from mod_tui.widgets.log_tail import LogTail


def _registry() -> WidgetRegistry:
    reg = WidgetRegistry()
    reg.register("OrchestratorChat", OrchestratorChat)
    reg.register("ActivityFeed", ActivityFeed)
    reg.register("LogTail", LogTail)
    return reg


class _Host(App):
    """Minimal App that holds a single panel-area Container as the apply target."""
    def compose(self) -> ComposeResult:
        yield TxContainer(id="panel-area")


def _spec_with_tabs() -> LayoutSpec:
    return LayoutSpec.model_validate({
        "version": 1,
        "layout": {
            "type": "horizontal",
            "children": [
                {"id": "orch", "widget": "OrchestratorChat"},
                {
                    "type": "tabs",
                    "children": [
                        {"id": "feed", "widget": "ActivityFeed", "title": "Activity"},
                        {"id": "logs", "widget": "LogTail", "title": "Logs", "props": {"file_path": "/dev/null"}},
                    ],
                    "active": "logs",
                },
            ],
        },
    })


@pytest.mark.asyncio
async def test_tabs_node_builds_into_tabbedcontent():
    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        area = app.query_one("#panel-area", TxContainer)
        await apply(area, _spec_with_tabs(), _registry())
        await pilot.pause()
        tcs = app.query(TabbedContent)
        assert len(tcs) == 1
        panes = tcs.first().query(TabPane)
        assert {p.id for p in panes} == {"tabpane-feed", "tabpane-logs"}


@pytest.mark.asyncio
async def test_tabs_active_field_selects_initial_pane():
    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        area = app.query_one("#panel-area", TxContainer)
        await apply(area, _spec_with_tabs(), _registry())
        await pilot.pause()
        tc = app.query_one(TabbedContent)
        assert tc.active == "tabpane-logs"


@pytest.mark.asyncio
async def test_tabs_panels_are_mounted_with_panel_id_format():
    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        area = app.query_one("#panel-area", TxContainer)
        await apply(area, _spec_with_tabs(), _registry())
        await pilot.pause()
        # Each child Panel inside Tabs gets the same `panel-<id>` id treatment
        # as a regular Panel, so set_layout / focus_panel still work uniformly.
        assert app.query_one("#panel-feed") is not None
        assert app.query_one("#panel-logs") is not None
