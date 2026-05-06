import asyncio

import pytest

from mod_tui.agents.request_inbox import RequestInbox


@pytest.mark.asyncio
async def test_register_and_resolve_round_trip():
    inbox = RequestInbox()
    request_id = inbox.register()

    async def resolver():
        await asyncio.sleep(0)
        inbox.resolve(request_id, "the answer")

    asyncio.create_task(resolver())
    result = await inbox.wait(request_id, timeout_s=1.0)
    assert result == "the answer"


@pytest.mark.asyncio
async def test_wait_times_out_when_no_resolution():
    inbox = RequestInbox()
    request_id = inbox.register()
    with pytest.raises(asyncio.TimeoutError):
        await inbox.wait(request_id, timeout_s=0.05)


@pytest.mark.asyncio
async def test_resolve_unknown_id_is_silently_ignored():
    inbox = RequestInbox()
    inbox.resolve("nonexistent", "ignored")  # must not raise


@pytest.mark.asyncio
async def test_register_returns_unique_ids():
    inbox = RequestInbox()
    a = inbox.register()
    b = inbox.register()
    assert a != b


@pytest.mark.asyncio
async def test_pending_returns_open_request_ids():
    inbox = RequestInbox()
    a = inbox.register()
    b = inbox.register()
    inbox.resolve(a, "done")
    await inbox.wait(a, timeout_s=1.0)  # drain
    assert inbox.pending() == [b]
