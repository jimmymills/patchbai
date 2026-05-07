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
