from pathlib import Path

import pytest
from textual.app import App
from textual.containers import Container

from mod_tui.layout.defaults import dashboard_layout
from mod_tui.layout.engine import apply as apply_layout
from mod_tui.layout.registry import WidgetRegistry
from mod_tui.layout.spec import LayoutSpec
from mod_tui.widgets.orchestrator_chat import OrchestratorChat
from mod_tui.widgets.placeholders import ActivityFeed
from mod_tui.widgets.agent_table import AgentTable


def _registry() -> WidgetRegistry:
    reg = WidgetRegistry()
    reg.register("OrchestratorChat", OrchestratorChat)
    reg.register("AgentTable", AgentTable)
    reg.register("ActivityFeed", ActivityFeed)
    return reg


class _HostApp(App):
    def compose(self):
        yield Container(id="panel-area")


def _spec_with_focus(focus: str) -> LayoutSpec:
    spec = dashboard_layout()
    return LayoutSpec.model_validate({**spec.model_dump(mode="json"), "focus": focus})


@pytest.mark.asyncio
async def test_apply_preserves_focused_panel_id_across_rebuilds(tmp_path: Path):
    app = _HostApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        area = app.query_one("#panel-area", Container)
        registry = _registry()

        # First apply: focus orchestrator.
        await apply_layout(area, _spec_with_focus("orch"), registry)
        await pilot.pause()
        focused_id = app.focused.id if app.focused else None
        assert focused_id == "panel-orch"

        # Second apply: same layout but no `focus` field — focus should
        # survive because the panel id "orch" is still present.
        spec_no_focus = LayoutSpec.model_validate({
            **dashboard_layout().model_dump(mode="json"),
            "focus": None,
        })
        await apply_layout(area, spec_no_focus, registry)
        await pilot.pause()
        focused_id_after = app.focused.id if app.focused else None
        assert focused_id_after == "panel-orch", (
            f"focus should survive rebuild; got {focused_id_after}"
        )
