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


@pytest.mark.asyncio
async def test_on_pending_changed_fires_when_inbox_becomes_non_empty():
    counts: list[int] = []
    inbox = RequestInbox(on_pending_changed=counts.append)
    inbox.register()
    assert counts == [1]


@pytest.mark.asyncio
async def test_on_pending_changed_fires_on_wait_drain_after_resolve():
    counts: list[int] = []
    inbox = RequestInbox(on_pending_changed=counts.append)
    rid = inbox.register()
    inbox.resolve(rid, "answer")
    await inbox.wait(rid, timeout_s=1.0)
    assert counts == [1, 0]


@pytest.mark.asyncio
async def test_on_pending_changed_fires_on_wait_timeout_drain():
    counts: list[int] = []
    inbox = RequestInbox(on_pending_changed=counts.append)
    rid = inbox.register()
    with pytest.raises(asyncio.TimeoutError):
        await inbox.wait(rid, timeout_s=0.05)
    assert counts == [1, 0]


@pytest.mark.asyncio
async def test_on_pending_changed_fires_on_every_transition_for_stacked_asks():
    """Stacked asks: every register/drain transition fires the callback.
    Counts go 0→1→2→1→0, so subscribers can distinguish 'still non-empty'
    (>=1) from 'now empty' (==0)."""
    counts: list[int] = []
    inbox = RequestInbox(on_pending_changed=counts.append)
    a = inbox.register()
    b = inbox.register()
    inbox.resolve(a, "a")
    await inbox.wait(a, timeout_s=1.0)
    inbox.resolve(b, "b")
    await inbox.wait(b, timeout_s=1.0)
    assert counts == [1, 2, 1, 0]


@pytest.mark.asyncio
async def test_on_pending_changed_callback_exception_is_swallowed():
    def boom(_count: int) -> None:
        raise RuntimeError("boom")

    inbox = RequestInbox(on_pending_changed=boom)
    # Must not raise.
    rid = inbox.register()
    inbox.resolve(rid, "ok")
    await inbox.wait(rid, timeout_s=1.0)
