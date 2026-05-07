import asyncio
from pathlib import Path

import pytest
from claude_agent_sdk import (
    PermissionResultAllow, PermissionResultDeny, ToolPermissionContext,
)

from patchbai.agents.fake_sdk_adapter import FakeSDKAdapter
from patchbai.agents.manager import AgentManager
from patchbai.agents.permission_grants import PermissionGrants
from patchbai.events import EventBus, PermissionRequested, PermissionResolved


def _ok_script():
    from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock
    return [
        AssistantMessage(content=[TextBlock(text="done")], model="m"),
        ResultMessage(
            subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
            num_turns=1, session_id="s", total_cost_usd=0.0,
            usage={"input_tokens": 1, "output_tokens": 1}, result="done",
        ),
    ]


@pytest.mark.asyncio
async def test_no_grants_keeps_bypass_permissions(tmp_path: Path):
    # Default constructor (no permission_grants kwarg) preserves today's
    # behavior — bypass for every child.
    manager = AgentManager(
        cwd=tmp_path, bus=EventBus(),
        adapter_factory=lambda: FakeSDKAdapter(scripts=[_ok_script()]),
    )
    aid = await manager.spawn(name="r", prompt="hi")
    info = next(i for i in manager.list_infos() if i.id == aid)
    opts = manager._build_options(info)
    assert opts.permission_mode == "bypassPermissions"
    assert opts.can_use_tool is None


@pytest.mark.asyncio
async def test_grants_provided_swaps_bypass_for_can_use_tool(tmp_path: Path):
    manager = AgentManager(
        cwd=tmp_path, bus=EventBus(),
        adapter_factory=lambda: FakeSDKAdapter(scripts=[_ok_script()]),
        permission_grants=PermissionGrants(cwd=tmp_path),
    )
    aid = await manager.spawn(name="r", prompt="hi")
    info = next(i for i in manager.list_infos() if i.id == aid)
    opts = manager._build_options(info)
    assert opts.permission_mode is None
    assert callable(opts.can_use_tool)


@pytest.mark.asyncio
async def test_callback_short_circuits_on_persistent_grant(tmp_path: Path):
    grants = PermissionGrants(cwd=tmp_path)
    grants.remember(agent_name="r", tool_name="Read", behavior="allow")
    bus = EventBus()
    requests: list[PermissionRequested] = []
    bus.subscribe(PermissionRequested, requests.append)

    manager = AgentManager(
        cwd=tmp_path, bus=bus,
        adapter_factory=lambda: FakeSDKAdapter(scripts=[_ok_script()]),
        permission_grants=grants,
    )
    aid = await manager.spawn(name="r", prompt="hi")
    info = next(i for i in manager.list_infos() if i.id == aid)
    callback = manager._build_options(info).can_use_tool

    ctx = ToolPermissionContext(tool_use_id="t1")
    result = await callback("Read", {"path": "x"}, ctx)
    assert isinstance(result, PermissionResultAllow)
    assert requests == []  # short-circuited; no UI involved


@pytest.mark.asyncio
async def test_callback_publishes_permission_requested_when_no_grant(tmp_path: Path):
    bus = EventBus()
    requests: list[PermissionRequested] = []
    bus.subscribe(PermissionRequested, requests.append)
    resolved: list[PermissionResolved] = []
    bus.subscribe(PermissionResolved, resolved.append)
    grants = PermissionGrants(cwd=tmp_path)

    manager = AgentManager(
        cwd=tmp_path, bus=bus,
        adapter_factory=lambda: FakeSDKAdapter(scripts=[_ok_script()]),
        permission_grants=grants,
    )
    aid = await manager.spawn(name="r", prompt="hi")
    info = next(i for i in manager.list_infos() if i.id == aid)
    callback = manager._build_options(info).can_use_tool
    ctx = ToolPermissionContext(tool_use_id="t1")

    async def driver():
        await asyncio.sleep(0)
        inbox = manager.get_permission_inbox(aid)
        pending = inbox.pending()
        assert pending and pending[0].tool_name == "Read"
        inbox.resolve(pending[0].request_id, PermissionResultAllow())

    driver_task = asyncio.create_task(driver())
    result = await callback("Read", {"path": "x"}, ctx)
    await driver_task

    assert isinstance(result, PermissionResultAllow)
    assert requests and requests[0].tool_name == "Read"
    assert requests[0].agent_id == aid
    assert requests[0].agent_name == "r"
    assert len(resolved) == 1
    assert resolved[0].agent_id == aid
    assert resolved[0].request_id == requests[0].request_id
    assert resolved[0].behavior == "allow"


@pytest.mark.asyncio
async def test_callback_returns_deny_when_inbox_cancelled(tmp_path: Path):
    bus = EventBus()
    resolved: list[PermissionResolved] = []
    bus.subscribe(PermissionResolved, resolved.append)
    grants = PermissionGrants(cwd=tmp_path)
    manager = AgentManager(
        cwd=tmp_path, bus=bus,
        adapter_factory=lambda: FakeSDKAdapter(scripts=[_ok_script()]),
        permission_grants=grants,
    )
    aid = await manager.spawn(name="r", prompt="hi")
    info = next(i for i in manager.list_infos() if i.id == aid)
    callback = manager._build_options(info).can_use_tool
    ctx = ToolPermissionContext(tool_use_id="t1")

    async def killer():
        await asyncio.sleep(0)
        manager.get_permission_inbox(aid).cancel_all()

    asyncio.create_task(killer())
    result = await callback("Read", {"path": "x"}, ctx)
    assert isinstance(result, PermissionResultDeny)
    assert len(resolved) == 1
    assert resolved[0].agent_id == aid
    assert resolved[0].behavior == "cancelled"
