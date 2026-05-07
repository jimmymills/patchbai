import json
from pathlib import Path

import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

from mod_tui.agents.fake_sdk_adapter import FakeSDKAdapter
from mod_tui.agents.manager import AgentManager
from mod_tui.app import ModTuiApp
from mod_tui.events import EventBus, WorkspaceCwdChanged
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


def _build_app(cwd):
    bus = EventBus()
    manager = AgentManager(
        cwd=cwd, bus=bus,
        adapter_factory=lambda: FakeSDKAdapter(scripts=[_ok()]),
    )
    app = ModTuiApp(cwd=cwd, manager=manager, global_dir=cwd / ".global")
    app.event_bus = bus
    app.orchestrator = OrchestratorSession(
        cwd=cwd, bus=bus, manager=manager,
        adapter=FakeSDKAdapter(scripts=[_ok()]),
        apply_layout=app._orchestrator_apply_layout,
        layouts_store=app.layouts_store,
        themes_store=app.themes_store,
        config_store=app.config_store,
        actions=app.actions_registry,
        rebind_keys=app._rebind_keys,
        widget_registry=app.registry,
        current_layout=lambda: app._active_layout(),
        app=app,
    )
    return app, bus


@pytest.mark.asyncio
async def test_change_cwd_swaps_cwd_and_publishes_event(tmp_path):
    proj_a = tmp_path / "a"
    proj_b = tmp_path / "b"
    proj_a.mkdir()
    proj_b.mkdir()
    app, bus = _build_app(proj_a)
    received: list[str] = []
    bus.subscribe(WorkspaceCwdChanged, lambda e: received.append(e.cwd))
    async with app.run_test() as pilot:
        await pilot.pause()
        # Re-supply a fresh adapter for the orchestrator restart.
        app.orchestrator._next_adapter_factory = (
            lambda: FakeSDKAdapter(scripts=[_ok()])
        )
        result = await app.change_cwd(proj_b)
        await pilot.pause()
        assert result == {"changed": str(proj_b.resolve())}
        assert app.cwd == proj_b.resolve()
        assert (proj_b / ".mod_tui" / "workspace.json").exists()
        assert received and received[-1] == str(proj_b.resolve())


@pytest.mark.asyncio
async def test_change_cwd_noop_for_same_path(tmp_path):
    proj = tmp_path / "p"
    proj.mkdir()
    app, _ = _build_app(proj)
    async with app.run_test() as pilot:
        await pilot.pause()
        result = await app.change_cwd(proj)
        assert result == {"unchanged": True}


@pytest.mark.asyncio
async def test_change_cwd_rejects_invalid_path(tmp_path):
    proj = tmp_path / "p"
    proj.mkdir()
    app, _ = _build_app(proj)
    async with app.run_test() as pilot:
        await pilot.pause()
        result = await app.change_cwd(tmp_path / "does-not-exist")
        assert "error" in result
        assert app.cwd == proj.resolve() or app.cwd == proj
