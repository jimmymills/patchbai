import asyncio
from pathlib import Path

import pytest
from claude_agent_sdk import (
    AssistantMessage, PermissionResultAllow, PermissionResultDeny,
    ResultMessage, TextBlock, ToolPermissionContext,
)

from patchbai.agents.fake_sdk_adapter import FakeSDKAdapter
from patchbai.agents.manager import AgentManager
from patchbai.agents.permission_grants import PermissionGrants
from patchbai.events import EventBus, PermissionRequested
from patchbai.orchestrator.session import OrchestratorSession


def _ok():
    return [
        AssistantMessage(content=[TextBlock(text="hi")], model="m"),
        ResultMessage(
            subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
            num_turns=1, session_id="s", total_cost_usd=0.0,
            usage={"input_tokens": 1, "output_tokens": 1}, result="hi",
        ),
    ]


@pytest.mark.asyncio
async def test_no_grants_keeps_orchestrator_bypass(tmp_path: Path):
    bus = EventBus()
    manager = AgentManager(
        cwd=tmp_path, bus=bus,
        adapter_factory=lambda: FakeSDKAdapter(scripts=[_ok()]),
    )
    orch = OrchestratorSession(
        cwd=tmp_path, bus=bus, manager=manager,
        adapter=FakeSDKAdapter(scripts=[_ok()]),
    )
    await orch.start()
    assert orch.permission_grants is None
    assert orch.get_permission_inbox() is None
    await orch.stop()


@pytest.mark.asyncio
async def test_grants_provided_attaches_callback_and_inbox(tmp_path: Path):
    bus = EventBus()
    grants = PermissionGrants(cwd=tmp_path)
    manager = AgentManager(
        cwd=tmp_path, bus=bus,
        adapter_factory=lambda: FakeSDKAdapter(scripts=[_ok()]),
        permission_grants=grants,
    )
    orch = OrchestratorSession(
        cwd=tmp_path, bus=bus, manager=manager,
        adapter=FakeSDKAdapter(scripts=[_ok()]),
        permission_grants=grants,
    )
    await orch.start()
    assert orch.permission_grants is grants
    inbox = orch.get_permission_inbox()
    assert inbox is not None
    callback = orch._can_use_tool_callback
    assert callable(callback)

    grants.remember(
        agent_name="orchestrator",
        tool_name="mcp__patchbai_orchestrator__list_widgets",
        behavior="allow",
    )
    ctx = ToolPermissionContext(tool_use_id="t1")
    result = await callback(
        "mcp__patchbai_orchestrator__list_widgets", {}, ctx,
    )
    assert isinstance(result, PermissionResultAllow)
    await orch.stop()


@pytest.mark.asyncio
async def test_orchestrator_callback_publishes_event_with_orchestrator_identity(
    tmp_path: Path,
):
    bus = EventBus()
    requests: list[PermissionRequested] = []
    bus.subscribe(PermissionRequested, requests.append)
    grants = PermissionGrants(cwd=tmp_path)
    manager = AgentManager(
        cwd=tmp_path, bus=bus,
        adapter_factory=lambda: FakeSDKAdapter(scripts=[_ok()]),
        permission_grants=grants,
    )
    orch = OrchestratorSession(
        cwd=tmp_path, bus=bus, manager=manager,
        adapter=FakeSDKAdapter(scripts=[_ok()]),
        permission_grants=grants,
    )
    await orch.start()
    callback = orch._can_use_tool_callback
    ctx = ToolPermissionContext(tool_use_id="t1")

    async def driver():
        await asyncio.sleep(0)
        inbox = orch.get_permission_inbox()
        pending = inbox.pending()
        inbox.resolve(pending[0].request_id, PermissionResultAllow())

    asyncio.create_task(driver())
    result = await callback("Bash", {"cmd": "ls"}, ctx)
    assert isinstance(result, PermissionResultAllow)

    assert requests
    assert requests[0].agent_id == "orchestrator"
    assert requests[0].agent_name == "orchestrator"
    await orch.stop()


@pytest.mark.asyncio
async def test_orchestrator_callback_returns_deny_when_inbox_cancelled(
    tmp_path: Path,
):
    bus = EventBus()
    grants = PermissionGrants(cwd=tmp_path)
    manager = AgentManager(
        cwd=tmp_path, bus=bus,
        adapter_factory=lambda: FakeSDKAdapter(scripts=[_ok()]),
        permission_grants=grants,
    )
    orch = OrchestratorSession(
        cwd=tmp_path, bus=bus, manager=manager,
        adapter=FakeSDKAdapter(scripts=[_ok()]),
        permission_grants=grants,
    )
    await orch.start()
    callback = orch._can_use_tool_callback
    ctx = ToolPermissionContext(tool_use_id="t1")

    async def canceller():
        await asyncio.sleep(0)
        orch.get_permission_inbox().cancel_all()

    asyncio.create_task(canceller())
    result = await callback("Bash", {"cmd": "ls"}, ctx)
    assert isinstance(result, PermissionResultDeny)
    assert "cancelled" in result.message
    await orch.stop()
