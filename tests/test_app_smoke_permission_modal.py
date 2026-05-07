from pathlib import Path

import pytest

from patchbai.app import PatchbaiApp


def test_default_constructs_grants_object(tmp_path: Path):
    app = PatchbaiApp(cwd=tmp_path, global_dir=tmp_path / "cfg")
    assert app._permission_grants is not None
    assert app.manager._grants is app._permission_grants
    assert app.orchestrator.permission_grants is app._permission_grants


def test_bypass_permissions_skips_grants(tmp_path: Path):
    app = PatchbaiApp(
        cwd=tmp_path, global_dir=tmp_path / "cfg",
        bypass_permissions=True,
    )
    assert app._permission_grants is None
    assert app.manager._grants is None
    assert app.orchestrator.permission_grants is None


@pytest.mark.asyncio
async def test_permission_request_pushes_modal_when_grants_present(tmp_path: Path):
    from patchbai.app import PatchbaiApp
    from patchbai.events import PermissionRequested
    from patchbai.widgets.permission_modal import PermissionModal

    app = PatchbaiApp(cwd=tmp_path, global_dir=tmp_path / "cfg")
    async with app.run_test() as pilot:
        await pilot.pause()
        app.event_bus.publish(PermissionRequested(
            agent_id="a1", agent_name="r", request_id="r1",
            tool_name="Read", tool_input={"path": "x"},
        ))
        await pilot.pause()
        assert isinstance(app.screen, PermissionModal)


@pytest.mark.asyncio
async def test_permission_request_does_nothing_when_bypass(tmp_path: Path):
    from patchbai.app import PatchbaiApp
    from patchbai.events import PermissionRequested
    from patchbai.widgets.permission_modal import PermissionModal

    app = PatchbaiApp(
        cwd=tmp_path, global_dir=tmp_path / "cfg",
        bypass_permissions=True,
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        app.event_bus.publish(PermissionRequested(
            agent_id="a1", agent_name="r", request_id="r1",
            tool_name="Read", tool_input={},
        ))
        await pilot.pause()
        assert not isinstance(app.screen, PermissionModal)


@pytest.mark.asyncio
async def test_first_request_renders_in_pushed_modal(tmp_path: Path):
    # Regression: the very first PermissionRequested that triggers the
    # modal push must reach the modal — otherwise it sits idle while the
    # SDK callback awaits forever. The fix is to thread the initial event
    # into the modal via constructor.
    from patchbai.app import PatchbaiApp
    from patchbai.events import PermissionRequested
    from patchbai.widgets.permission_modal import PermissionModal

    app = PatchbaiApp(cwd=tmp_path, global_dir=tmp_path / "cfg")
    async with app.run_test() as pilot:
        await pilot.pause()
        app.event_bus.publish(PermissionRequested(
            agent_id="a1", agent_name="researcher", request_id="r1",
            tool_name="Read", tool_input={"path": "x"},
        ))
        await pilot.pause()
        assert isinstance(app.screen, PermissionModal)
        assert app.screen._current_request is not None
        assert app.screen._current_request.tool_name == "Read"


@pytest.mark.asyncio
async def test_e2e_child_allow_once_unblocks_callback(tmp_path: Path):
    import asyncio
    from claude_agent_sdk import (
        AssistantMessage, PermissionResultAllow, ResultMessage,
        TextBlock, ToolPermissionContext,
    )
    from patchbai.app import PatchbaiApp
    from patchbai.agents.fake_sdk_adapter import FakeSDKAdapter

    app = PatchbaiApp(cwd=tmp_path, global_dir=tmp_path / "cfg")
    async with app.run_test() as pilot:
        await pilot.pause()
        app.manager._adapter_factory = lambda: FakeSDKAdapter(scripts=[[
            AssistantMessage(content=[TextBlock(text="x")], model="m"),
            ResultMessage(
                subtype="success", duration_ms=1, duration_api_ms=1,
                is_error=False, num_turns=1, session_id="s",
                total_cost_usd=0.0, usage={"input_tokens": 1, "output_tokens": 1},
                result="x",
            ),
        ]])
        aid = await app.manager.spawn(name="researcher", prompt="hi")
        await app.manager.wait_idle(aid)

        info = next(i for i in app.manager.list_infos() if i.id == aid)
        callback = app.manager._build_options(info).can_use_tool
        ctx = ToolPermissionContext(tool_use_id="t1")

        cb_task = asyncio.create_task(callback("Read", {"path": "x"}, ctx))
        for _ in range(20):
            await pilot.pause()
            from patchbai.widgets.permission_modal import PermissionModal
            if isinstance(app.screen, PermissionModal):
                break
        await pilot.click("#allow-once")
        await pilot.pause()
        result = await asyncio.wait_for(cb_task, timeout=2.0)
        assert isinstance(result, PermissionResultAllow)
