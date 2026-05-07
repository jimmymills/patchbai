import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock
from textual.containers import Container as TxContainer
from textual.widgets import TabbedContent, TabPane

from mod_tui.agents.fake_sdk_adapter import FakeSDKAdapter
from mod_tui.agents.manager import AgentManager
from mod_tui.app import ModTuiApp
from mod_tui.events import EventBus
from mod_tui.orchestrator.session import OrchestratorSession


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
    app = ModTuiApp(cwd=tmp_path, manager=manager, global_dir=tmp_path)
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
async def test_app_starts_with_one_tab_when_no_workspace_exists(tmp_path):
    app = _build_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        tc = app.query_one("#app-tabs", TabbedContent)
        panes = tc.query(TabPane)
        assert len(panes) == 1
        # The default tab's panel-area container is seeded with id "default".
        area = app.query_one("#panel-area-default", TxContainer)
        assert area.id is not None and area.id.startswith("panel-area-")


@pytest.mark.asyncio
async def test_app_seeds_dashboard_layout_on_first_run(tmp_path):
    app = _build_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.query_one("#panel-orch") is not None


@pytest.mark.asyncio
async def test_app_writes_workspace_json_on_launch(tmp_path):
    app = _build_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        ws_path = tmp_path / ".mod_tui" / "workspace.json"
        assert ws_path.exists()
