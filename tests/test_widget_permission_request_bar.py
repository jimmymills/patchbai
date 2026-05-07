from pathlib import Path

import pytest
from textual.app import App, ComposeResult

from patchbai.agents.permission_grants import PermissionGrants
from patchbai.agents.permission_inbox import PermissionInbox
from patchbai.events import EventBus, PermissionRequested, PermissionResolved
from patchbai.widgets.agent_transcript import AgentTranscript


class _Host(App):
    def __init__(self, *, bus, inboxes, grants):
        super().__init__()
        self.event_bus = bus
        self._inboxes = inboxes
        class _StubManager:
            def get_permission_inbox(_self, aid):
                return inboxes.get(aid)
        self.manager = _StubManager()
        self._permission_grants = grants

    def compose(self) -> ComposeResult:
        yield AgentTranscript(agent_id="a1", event_bus=self.event_bus)


def _request(rid="r1"):
    return PermissionRequested(
        agent_id="a1", agent_name="researcher", request_id=rid,
        tool_name="Read", tool_input={"path": "x"},
    )


@pytest.mark.asyncio
async def test_bar_appears_on_request_for_this_agent(tmp_path: Path):
    bus = EventBus()
    inbox = PermissionInbox()
    rid = inbox.register(tool_name="Read", tool_input={"path": "x"})
    grants = PermissionGrants(cwd=tmp_path)
    app = _Host(bus=bus, inboxes={"a1": inbox}, grants=grants)
    async with app.run_test() as pilot:
        await pilot.pause()
        bus.publish(_request(rid=rid))
        await pilot.pause()
        from patchbai.widgets.permission_request_bar import PermissionRequestBar
        bars = app.query(PermissionRequestBar)
        assert len(bars) == 1


@pytest.mark.asyncio
async def test_bar_does_not_appear_for_other_agents(tmp_path: Path):
    bus = EventBus()
    other_inbox = PermissionInbox()
    rid = other_inbox.register(tool_name="Read", tool_input={})
    grants = PermissionGrants(cwd=tmp_path)
    app = _Host(bus=bus, inboxes={"a2": other_inbox}, grants=grants)
    async with app.run_test() as pilot:
        await pilot.pause()
        bus.publish(PermissionRequested(
            agent_id="a2", agent_name="other", request_id=rid,
            tool_name="Read", tool_input={},
        ))
        await pilot.pause()
        from patchbai.widgets.permission_request_bar import PermissionRequestBar
        assert len(app.query(PermissionRequestBar)) == 0


@pytest.mark.asyncio
async def test_bar_allow_button_resolves_inbox(tmp_path: Path):
    from claude_agent_sdk import PermissionResultAllow
    bus = EventBus()
    inbox = PermissionInbox()
    rid = inbox.register(tool_name="Read", tool_input={})
    grants = PermissionGrants(cwd=tmp_path)
    app = _Host(bus=bus, inboxes={"a1": inbox}, grants=grants)
    async with app.run_test() as pilot:
        await pilot.pause()
        bus.publish(_request(rid=rid))
        await pilot.pause()
        await pilot.click("#bar-allow-once")
        await pilot.pause()
        fut = inbox._futures.get(rid)
        assert fut is None or (fut.done() and isinstance(fut.result(), PermissionResultAllow))


@pytest.mark.asyncio
async def test_bar_clears_when_resolution_comes_externally(tmp_path: Path):
    bus = EventBus()
    inbox = PermissionInbox()
    rid = inbox.register(tool_name="Read", tool_input={})
    grants = PermissionGrants(cwd=tmp_path)
    app = _Host(bus=bus, inboxes={"a1": inbox}, grants=grants)
    async with app.run_test() as pilot:
        await pilot.pause()
        bus.publish(_request(rid=rid))
        await pilot.pause()
        from patchbai.widgets.permission_request_bar import PermissionRequestBar
        assert len(app.query(PermissionRequestBar)) == 1
        bus.publish(PermissionResolved(
            agent_id="a1", request_id=rid, behavior="allow",
        ))
        await pilot.pause()
        assert len(app.query(PermissionRequestBar)) == 0
