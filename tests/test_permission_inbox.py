import asyncio

import pytest
from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

from patchbai.agents.permission_inbox import PermissionInbox


async def _resolve_soon(inbox, rid, result):
    await asyncio.sleep(0)
    inbox.resolve(rid, result)


@pytest.mark.asyncio
async def test_register_and_resolve_round_trip():
    inbox = PermissionInbox()
    request_id = inbox.register(tool_name="Read", tool_input={"path": "x"})
    asyncio.create_task(_resolve_soon(inbox, request_id, PermissionResultAllow()))
    result = await inbox.wait(request_id, timeout_s=1.0)
    assert isinstance(result, PermissionResultAllow)


@pytest.mark.asyncio
async def test_pending_returns_open_requests():
    inbox = PermissionInbox()
    a = inbox.register(tool_name="Read", tool_input={})
    b = inbox.register(tool_name="Bash", tool_input={"cmd": "ls"})
    pending = inbox.pending()
    assert {p.request_id for p in pending} == {a, b}
    assert {p.tool_name for p in pending} == {"Read", "Bash"}


@pytest.mark.asyncio
async def test_on_pending_changed_fires_for_each_transition():
    counts: list[int] = []
    inbox = PermissionInbox(on_pending_changed=counts.append)
    rid = inbox.register(tool_name="Read", tool_input={})
    inbox.resolve(rid, PermissionResultAllow())
    await inbox.wait(rid, timeout_s=1.0)
    assert counts == [1, 0]


@pytest.mark.asyncio
async def test_cancel_all_marks_pending_futures_cancelled():
    inbox = PermissionInbox()
    rid = inbox.register(tool_name="Read", tool_input={})
    inbox.cancel_all()
    with pytest.raises(asyncio.CancelledError):
        await inbox.wait(rid, timeout_s=1.0)


@pytest.mark.asyncio
async def test_resolve_unknown_id_is_silently_ignored():
    inbox = PermissionInbox()
    inbox.resolve("nope", PermissionResultAllow())  # must not raise
