import asyncio

import pytest

from mod_tui.agents.child_tools import build_child_tools
from mod_tui.agents.request_inbox import RequestInbox
from mod_tui.events import (
    AgentNotifiedOrchestrator,
    EventBus,
)


@pytest.mark.asyncio
async def test_notify_orchestrator_publishes_event_and_returns():
    bus = EventBus()
    received: list[AgentNotifiedOrchestrator] = []
    bus.subscribe(AgentNotifiedOrchestrator, received.append)

    inbox = RequestInbox()
    notify, _ask = build_child_tools(agent_id="a1", bus=bus, inbox=inbox)

    out = await notify({"message": "tests passed"})

    assert received == [AgentNotifiedOrchestrator(agent_id="a1", message="tests passed")]
    assert "delivered" in out["content"][0]["text"].lower()


@pytest.mark.asyncio
async def test_ask_orchestrator_blocks_until_inbox_resolves():
    bus = EventBus()
    inbox = RequestInbox()
    _notify, ask = build_child_tools(agent_id="a1", bus=bus, inbox=inbox)

    async def resolver():
        await asyncio.sleep(0)
        # The first pending request id is the one ask just registered.
        pending = inbox.pending()
        assert len(pending) == 1
        inbox.resolve(pending[0], "ship it")

    asyncio.create_task(resolver())
    out = await ask({"question": "go/no-go?"})
    assert out["content"][0]["text"] == "ship it"


@pytest.mark.asyncio
async def test_ask_orchestrator_times_out():
    bus = EventBus()
    inbox = RequestInbox()
    _notify, ask = build_child_tools(agent_id="a1", bus=bus, inbox=inbox)

    out = await ask({"question": "anyone there?", "timeout_s": 0.05})
    text = out["content"][0]["text"].lower()
    assert "timeout" in text or "timed out" in text
