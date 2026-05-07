import pytest
from textual.app import App
from textual.containers import Container, Horizontal

from mod_tui.events import EventBus
from mod_tui.layout.engine import apply as apply_layout
from mod_tui.layout.registry import WidgetRegistry
from mod_tui.layout.spec import LayoutSpec
from mod_tui.layout.splitter import Splitter
from mod_tui.widgets.agent_table import AgentTable
from mod_tui.widgets.orchestrator_chat import OrchestratorChat
from mod_tui.widgets.placeholders import ActivityFeed


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
    return reg


def _spec_three_horizontal() -> LayoutSpec:
    return LayoutSpec.model_validate({
        "version": 1,
        "layout": {
            "type": "horizontal",
            "children": [
                {"id": "orch", "widget": "OrchestratorChat"},
                {"id": "agents", "widget": "AgentTable"},
                {"id": "feed", "widget": "ActivityFeed"},
            ],
        },
    })


@pytest.mark.asyncio
async def test_splitters_interleaved_between_siblings():
    bus = EventBus()
    app = _HostApp(bus)
    async with app.run_test() as pilot:
        host = app.query_one("#panel-area", Container)
        await apply_layout(host, _spec_three_horizontal(), _registry())
        await pilot.pause()

        box = host.children[0]
        assert isinstance(box, Horizontal)
        kinds = [type(c).__name__ for c in box.children]
        # Three panels with splitters in between → 5 children total.
        assert len(box.children) == 5
        assert kinds[1] == "Splitter" and kinds[3] == "Splitter"


@pytest.mark.asyncio
async def test_single_child_container_has_no_splitter():
    spec = LayoutSpec.model_validate({
        "version": 1,
        "layout": {
            "type": "horizontal",
            "children": [
                {"id": "orch", "widget": "OrchestratorChat"},
            ],
        },
    })
    bus = EventBus()
    app = _HostApp(bus)
    async with app.run_test() as pilot:
        host = app.query_one("#panel-area", Container)
        await apply_layout(host, spec, _registry())
        await pilot.pause()
        box = host.children[0]
        assert len(box.children) == 1
        assert not any(isinstance(c, Splitter) for c in box.children)


@pytest.mark.asyncio
async def test_splitter_drag_resizes_neighbors():
    """Directly drive the splitter's mouse handlers and assert that neighbor
    inline width styles update with the drag delta."""
    bus = EventBus()
    app = _HostApp(bus)
    async with app.run_test() as pilot:
        host = app.query_one("#panel-area", Container)
        await apply_layout(host, _spec_three_horizontal(), _registry())
        await pilot.pause()

        box = host.children[0]
        splitter = next(c for c in box.children if isinstance(c, Splitter))
        prev_sib = box.children[0]
        next_sib = box.children[2]

        prev_w0 = prev_sib.size.width
        next_w0 = next_sib.size.width
        assert prev_w0 > 1 and next_w0 > 1

        # Simulate the start of a drag without going through the real mouse
        # capture machinery — set the same internal state that on_mouse_down
        # would set, then invoke on_mouse_move with a synthetic event.
        splitter._drag_start = (10, 0)
        splitter._initial_prev = prev_w0
        splitter._initial_next = next_w0

        class _Evt:
            def __init__(self, sx, sy):
                self.screen_x = sx
                self.screen_y = sy
            def stop(self):
                pass

        def _w(widget):
            return widget.styles.width.value  # type: ignore[union-attr]

        splitter.on_mouse_move(_Evt(15, 0))  # type: ignore[arg-type]  # +5 cells
        await pilot.pause()
        assert _w(prev_sib) == prev_w0 + 5
        assert _w(next_sib) == next_w0 - 5

        splitter.on_mouse_move(_Evt(5, 0))  # type: ignore[arg-type]  # net -5 from start
        await pilot.pause()
        assert _w(prev_sib) == prev_w0 - 5
        assert _w(next_sib) == next_w0 + 5
