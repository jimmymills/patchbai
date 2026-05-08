import pytest
from textual.app import App
from textual.containers import Container
from textual.widget import Widget
from textual.widgets import Static

from patchbai.events import EventBus
from patchbai.layout.engine import apply as apply_layout
from patchbai.layout.registry import WidgetRegistry
from patchbai.layout.spec import LayoutSpec
from patchbai.widgets.agent_table import AgentTable
from patchbai.widgets.orchestrator_chat import OrchestratorChat
from patchbai.widgets.activity_feed import ActivityFeed


class _BorderlessCustom(Static):
    """Custom widget with no border rule — should get the engine safety net."""


class _BorderedCustom(Static):
    DEFAULT_CSS = """
    _BorderedCustom {
        border: heavy red;
    }
    """


class _HostApp(App):
    def __init__(self, bus: EventBus) -> None:
        super().__init__()
        self.event_bus = bus

    def compose(self):
        yield Container(id="panel-area")


def _registry() -> WidgetRegistry:
    reg = WidgetRegistry()
    reg.register("OrchestratorChat", OrchestratorChat)
    reg.register("AgentTable", AgentTable)
    reg.register("ActivityFeed", ActivityFeed)
    reg.register("BorderlessCustom", _BorderlessCustom)
    reg.register("BorderedCustom", _BorderedCustom)
    return reg


@pytest.mark.asyncio
async def test_engine_assigns_default_border_title_from_class_attr():
    bus = EventBus()
    app = _HostApp(bus)
    async with app.run_test() as pilot:
        await pilot.pause()
        area = app.query_one("#panel-area", Container)
        spec = LayoutSpec.model_validate({
            "version": 1,
            "layout": {
                "type": "horizontal",
                "children": [
                    {"id": "orch", "widget": "OrchestratorChat"},
                    {"id": "feed", "widget": "ActivityFeed"},
                ],
            },
        })
        await apply_layout(area, spec, _registry())
        await pilot.pause()
        assert app.query_one("#panel-orch").border_title == "Orchestrator"
        assert app.query_one("#panel-feed").border_title == "Activity"


@pytest.mark.asyncio
async def test_engine_explicit_panel_title_overrides_default():
    bus = EventBus()
    app = _HostApp(bus)
    async with app.run_test() as pilot:
        await pilot.pause()
        area = app.query_one("#panel-area", Container)
        spec = LayoutSpec.model_validate({
            "version": 1,
            "layout": {
                "type": "horizontal",
                "children": [
                    {"id": "orch", "widget": "OrchestratorChat", "title": "Boss"},
                    {"id": "feed", "widget": "ActivityFeed"},
                ],
            },
        })
        await apply_layout(area, spec, _registry())
        await pilot.pause()
        assert app.query_one("#panel-orch").border_title == "Boss"


@pytest.mark.asyncio
async def test_engine_safety_net_applies_border_to_borderless_custom_widget():
    bus = EventBus()
    app = _HostApp(bus)
    async with app.run_test() as pilot:
        await pilot.pause()
        area = app.query_one("#panel-area", Container)
        spec = LayoutSpec.model_validate({
            "version": 1,
            "layout": {
                "type": "horizontal",
                "children": [
                    {"id": "orch", "widget": "OrchestratorChat"},
                    {"id": "x", "widget": "BorderlessCustom"},
                ],
            },
        })
        await apply_layout(area, spec, _registry())
        await pilot.pause()
        widget = app.query_one("#panel-x")
        # Inline border was set by the engine.
        assert widget.styles.has_rule("border_top")
        # Title still falls through to class name.
        assert widget.border_title == "_BorderlessCustom"


@pytest.mark.asyncio
async def test_engine_does_not_override_widgets_with_their_own_border():
    bus = EventBus()
    app = _HostApp(bus)
    async with app.run_test() as pilot:
        await pilot.pause()
        area = app.query_one("#panel-area", Container)
        spec = LayoutSpec.model_validate({
            "version": 1,
            "layout": {
                "type": "horizontal",
                "children": [
                    {"id": "orch", "widget": "OrchestratorChat"},
                    {"id": "x", "widget": "BorderedCustom"},
                ],
            },
        })
        await apply_layout(area, spec, _registry())
        await pilot.pause()
        widget = app.query_one("#panel-x")
        # Engine must NOT have set an inline border (the DEFAULT_CSS one wins).
        assert not widget._inline_styles.has_rule("border_top")


@pytest.mark.asyncio
async def test_engine_buggy_default_border_title_does_not_abort_apply():
    class Boom(Static):
        @classmethod
        def default_border_title(cls, props):
            raise RuntimeError("boom")

    bus = EventBus()
    app = _HostApp(bus)
    async with app.run_test() as pilot:
        await pilot.pause()
        area = app.query_one("#panel-area", Container)
        reg = _registry()
        reg.register("Boom", Boom)
        spec = LayoutSpec.model_validate({
            "version": 1,
            "layout": {
                "type": "horizontal",
                "children": [
                    {"id": "orch", "widget": "OrchestratorChat"},
                    {"id": "x", "widget": "Boom"},
                ],
            },
        })
        await apply_layout(area, spec, reg)
        await pilot.pause()
        # Apply succeeded; widget mounted; title fell back to class name.
        assert app.query_one("#panel-x").border_title == "Boom"
