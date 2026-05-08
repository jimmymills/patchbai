import gc

import pytest
from textual.app import App
from textual.containers import Container

from patchfeld.events import EventBus
from patchfeld.layout import engine as engine_mod
from patchfeld.layout.defaults import dashboard_layout
from patchfeld.layout.engine import apply as apply_layout
from patchfeld.layout.registry import WidgetRegistry
from patchfeld.widgets.agent_table import AgentTable
from patchfeld.widgets.orchestrator_chat import OrchestratorChat
from patchfeld.widgets.activity_feed import ActivityFeed


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
async def test_last_applied_spec_does_not_leak_after_container_gc():
    """After multiple App lifecycles, the cache must not grow unboundedly."""
    initial_size = len(engine_mod._last_applied_spec)

    for _ in range(5):
        bus = EventBus()
        app = _HostApp(bus)
        async with app.run_test() as pilot:
            await pilot.pause()
            area = app.query_one("#panel-area", Container)
            await apply_layout(area, dashboard_layout(), _registry())
            await pilot.pause()
        gc.collect()

    final_size = len(engine_mod._last_applied_spec)
    assert final_size <= initial_size + 1, (
        f"_last_applied_spec leaked: {initial_size=} → {final_size=}"
    )
