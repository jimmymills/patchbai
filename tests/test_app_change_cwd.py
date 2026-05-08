import json
from pathlib import Path

import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

from patchfeld.agents.fake_sdk_adapter import FakeSDKAdapter
from patchfeld.agents.manager import AgentManager
from patchfeld.agents.state import AgentState
from patchfeld.app import PatchfeldApp
from patchfeld.events import EventBus, WorkspaceCwdChanged
from patchfeld.orchestrator.session import OrchestratorSession


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
    app = PatchfeldApp(cwd=cwd, manager=manager, global_dir=cwd / ".global")
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
        assert (proj_b / ".patchfeld" / "workspace.json").exists()
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


@pytest.mark.asyncio
async def test_change_cwd_refuses_with_running_children(tmp_path):
    proj_a = tmp_path / "a"
    proj_b = tmp_path / "b"
    proj_a.mkdir()
    proj_b.mkdir()
    app, _ = _build_app(proj_a)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Spawn a child agent. The FakeSDKAdapter's stream completes when its
        # script is exhausted, so the AgentSession transitions to DONE on its
        # own. To simulate a "still running" child, we force the state back
        # to RUNNING after spawn — the refusal check in App.change_cwd just
        # inspects info.state.is_terminal on each AgentInfo.
        manager = app.manager
        manager._adapter_factory = lambda: FakeSDKAdapter(
            scripts=[[
                AssistantMessage(content=[TextBlock(text="hi")], model="fake-model"),
            ]],
        )
        await manager.spawn(name="worker", prompt="do thing")
        await pilot.pause()
        infos = manager.list_infos()
        assert infos, "spawn should register an agent info"
        infos[0].state = AgentState.RUNNING
        result = await app.change_cwd(proj_b)
        assert result.get("error") == "agents_running"
        assert result.get("agents") and result["agents"][0]["name"] == "worker"
        assert app.cwd == proj_a.resolve() or app.cwd == proj_a


@pytest.mark.asyncio
async def test_ctrl_shift_d_opens_change_cwd_modal(tmp_path):
    proj = tmp_path / "p"
    proj.mkdir()
    app, _ = _build_app(proj)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("ctrl+shift+d")
        await pilot.pause()
        from patchfeld.widgets.change_cwd_screen import ChangeCwdScreen
        assert isinstance(app.screen, ChangeCwdScreen)


@pytest.mark.asyncio
async def test_slash_cd_changes_cwd(tmp_path):
    proj_a = tmp_path / "a"
    proj_b = tmp_path / "b"
    proj_a.mkdir()
    proj_b.mkdir()
    app, bus = _build_app(proj_a)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.orchestrator._next_adapter_factory = (
            lambda: FakeSDKAdapter(scripts=[_ok()])
        )
        from patchfeld.events import UserMessageToOrchestrator
        bus.publish(UserMessageToOrchestrator(f"/cd {proj_b}"))
        await pilot.pause()
        await pilot.pause()  # second pause: change_cwd creates async tasks
        assert app.cwd == proj_b.resolve()


@pytest.mark.asyncio
async def test_change_cwd_updates_footer(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    proj_a = tmp_path / "a"
    proj_b = tmp_path / "b" / "deeper"
    proj_a.mkdir()
    proj_b.mkdir(parents=True)
    app, _ = _build_app(proj_a)
    async with app.run_test() as pilot:
        await pilot.pause()
        from patchfeld.widgets.chrome import StatusBar
        from textual.widgets import Static
        bar = app.query_one(StatusBar)
        assert "~/a" in str(bar.query_one("#sb-cwd", Static).content)
        app.orchestrator._next_adapter_factory = (
            lambda: FakeSDKAdapter(scripts=[_ok()])
        )
        await app.change_cwd(proj_b)
        await pilot.pause()
        text = str(bar.query_one("#sb-cwd", Static).content)
        assert "~/b/deeper" in text
