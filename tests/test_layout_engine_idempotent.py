import pytest
from textual.app import App
from textual.containers import Container

from mod_tui.events import EventBus, LayoutApplied, LayoutFailed
from mod_tui.layout.defaults import dashboard_layout
from mod_tui.layout.engine import apply as apply_layout
from mod_tui.layout.registry import WidgetRegistry
from mod_tui.layout.spec import LayoutSpec
from mod_tui.widgets.agent_table import AgentTable
from mod_tui.widgets.orchestrator_chat import OrchestratorChat
from mod_tui.widgets.placeholders import ActivityFeed


def _registry() -> WidgetRegistry:
    reg = WidgetRegistry()
    reg.register("OrchestratorChat", OrchestratorChat)
    reg.register("AgentTable", AgentTable)
    reg.register("ActivityFeed", ActivityFeed)
    return reg


class _HostApp(App):
    def __init__(self, bus: EventBus) -> None:
        super().__init__()
        self.event_bus = bus

    def compose(self):
        yield Container(id="panel-area")


@pytest.mark.asyncio
async def test_apply_publishes_layout_applied_event():
    bus = EventBus()
    applied: list[LayoutApplied] = []
    bus.subscribe(LayoutApplied, applied.append)

    app = _HostApp(bus)
    async with app.run_test() as pilot:
        await pilot.pause()
        area = app.query_one("#panel-area", Container)
        await apply_layout(area, dashboard_layout(), _registry())
        await pilot.pause()

    assert len(applied) == 1
    assert applied[0].spec == dashboard_layout()


@pytest.mark.asyncio
async def test_apply_idempotent_when_spec_unchanged_no_remount():
    bus = EventBus()
    applied: list[LayoutApplied] = []
    bus.subscribe(LayoutApplied, applied.append)

    app = _HostApp(bus)
    async with app.run_test() as pilot:
        await pilot.pause()
        area = app.query_one("#panel-area", Container)

        await apply_layout(area, dashboard_layout(), _registry())
        await pilot.pause()
        first_chat = app.query_one(OrchestratorChat)

        # Re-apply identical spec: no remount.
        await apply_layout(area, dashboard_layout(), _registry())
        await pilot.pause()
        second_chat = app.query_one(OrchestratorChat)

    assert first_chat is second_chat, "identical spec must skip the rebuild"
    # Two LayoutApplied events still fire — the apply is idempotent in effect,
    # not in side-effect-suppression. Subscribers can dedupe if needed.
    assert len(applied) == 2


@pytest.mark.asyncio
async def test_apply_publishes_layout_failed_on_unknown_widget():
    bus = EventBus()
    failed: list[LayoutFailed] = []
    bus.subscribe(LayoutFailed, failed.append)

    app = _HostApp(bus)
    async with app.run_test() as pilot:
        await pilot.pause()
        area = app.query_one("#panel-area", Container)
        registry = _registry()

        bad_spec = LayoutSpec.model_validate({
            "version": 1,
            "layout": {
                "type": "horizontal",
                "children": [
                    {"id": "orch", "widget": "OrchestratorChat"},
                    {"id": "x", "widget": "DoesNotExist"},
                ],
            },
        })

        with pytest.raises(Exception):
            await apply_layout(area, bad_spec, registry)
        await pilot.pause()

    assert len(failed) == 1
    assert "DoesNotExist" in failed[0].error
