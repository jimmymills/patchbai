import pytest
from pathlib import Path

from patchbai.app import PatchbaiApp
from patchbai.agents.fake_sdk_adapter import FakeSDKAdapter
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock


def _ok():
    return [
        AssistantMessage(content=[TextBlock(text="ok")], model="m"),
        ResultMessage(
            subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
            num_turns=1, session_id="s", total_cost_usd=0.0,
            usage={"input_tokens": 1, "output_tokens": 1}, result="ok",
        ),
    ]


@pytest.mark.asyncio
async def test_default_to_bypass_swaps_grants_to_none(tmp_path: Path):
    app = PatchbaiApp(cwd=tmp_path, global_dir=tmp_path / "cfg")
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app._permission_grants is not None
        assert app.manager._grants is not None
        result = await app.set_bypass_permissions(bypass=True)
        assert result == {"changed": "bypass"}
        assert app._permission_grants is None
        assert app.manager._grants is None
        assert app.orchestrator.permission_grants is None


@pytest.mark.asyncio
async def test_bypass_to_require_constructs_grants(tmp_path: Path):
    app = PatchbaiApp(
        cwd=tmp_path, global_dir=tmp_path / "cfg",
        bypass_permissions=True,
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app._permission_grants is None
        result = await app.set_bypass_permissions(bypass=False)
        assert result == {"changed": "require"}
        assert app._permission_grants is not None
        assert app.manager._grants is app._permission_grants
        assert app.orchestrator.permission_grants is app._permission_grants


@pytest.mark.asyncio
async def test_same_mode_returns_unchanged(tmp_path: Path):
    app = PatchbaiApp(cwd=tmp_path, global_dir=tmp_path / "cfg")
    async with app.run_test() as pilot:
        await pilot.pause()
        result = await app.set_bypass_permissions(bypass=False)
        assert result == {"unchanged": True}


@pytest.mark.asyncio
async def test_refuses_when_children_running(tmp_path: Path):
    app = PatchbaiApp(cwd=tmp_path, global_dir=tmp_path / "cfg")
    async with app.run_test() as pilot:
        await pilot.pause()
        app.manager._adapter_factory = lambda: FakeSDKAdapter(scripts=[_ok()])
        aid = await app.manager.spawn(name="researcher", prompt="hi")
        # Don't wait_idle — keep agent in non-terminal state.
        # Coerce state to RUNNING to simulate active.
        from patchbai.agents.state import AgentState
        app.manager.get_session(aid).info.state = AgentState.RUNNING
        result = await app.set_bypass_permissions(bypass=True)
        assert result.get("error") == "agents_running"
        assert any(a["name"] == "researcher" for a in result.get("agents", []))
        # Mode wasn't changed.
        assert app._permission_grants is not None
