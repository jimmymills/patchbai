from pathlib import Path

import pytest
from textual.app import App, ComposeResult

from patchbai.agents.permission_grants import PermissionGrants
from patchbai.agents.permission_inbox import PermissionInbox
from patchbai.events import EventBus, PermissionRequested
from patchbai.widgets.permission_modal import PermissionModal


class _Host(App):
    def __init__(self, *, bus, inbox, grants, agent_name="researcher"):
        super().__init__()
        self.event_bus = bus
        self._inbox = inbox
        self._grants = grants
        self._agent_name = agent_name

    def compose(self) -> ComposeResult:
        from textual.widgets import Input
        yield Input()

    async def on_mount(self) -> None:
        await self.push_screen(PermissionModal(
            inbox_lookup=lambda aid: self._inbox,
            grants=self._grants,
        ))


def _request(rid="r1", aid="a1", agent_name="researcher", tool="Read"):
    return PermissionRequested(
        agent_id=aid, agent_name=agent_name, request_id=rid,
        tool_name=tool, tool_input={"path": "x"},
        title=f"Claude wants to {tool}", description=None,
    )


@pytest.mark.asyncio
async def test_modal_renders_pending_request(tmp_path: Path):
    bus = EventBus()
    inbox = PermissionInbox()
    grants = PermissionGrants(cwd=tmp_path)
    rid = inbox.register(tool_name="Read", tool_input={"path": "x"})
    app = _Host(bus=bus, inbox=inbox, grants=grants)
    async with app.run_test() as pilot:
        await pilot.pause()
        bus.publish(_request(rid=rid))
        await pilot.pause()
        assert app.screen._current_request is not None
        assert app.screen._current_request.tool_name == "Read"


@pytest.mark.asyncio
async def test_allow_once_resolves_inbox_with_allow(tmp_path: Path):
    from claude_agent_sdk import PermissionResultAllow
    bus = EventBus()
    inbox = PermissionInbox()
    rid = inbox.register(tool_name="Read", tool_input={})
    grants = PermissionGrants(cwd=tmp_path)

    app = _Host(bus=bus, inbox=inbox, grants=grants)
    async with app.run_test() as pilot:
        await pilot.pause()
        bus.publish(_request(rid=rid))
        await pilot.pause()
        await pilot.click("#allow-once")
        await pilot.pause()
    fut = inbox._futures.get(rid)
    assert fut is None or (fut.done() and isinstance(fut.result(), PermissionResultAllow))


@pytest.mark.asyncio
async def test_always_allow_for_named_agent_writes_disk(tmp_path: Path):
    bus = EventBus()
    inbox = PermissionInbox()
    rid = inbox.register(tool_name="Read", tool_input={})
    grants = PermissionGrants(cwd=tmp_path)

    app = _Host(bus=bus, inbox=inbox, grants=grants)
    async with app.run_test() as pilot:
        await pilot.pause()
        bus.publish(_request(rid=rid))
        await pilot.pause()
        await pilot.click("#allow-always")
        await pilot.pause()
    fresh = PermissionGrants(cwd=tmp_path)
    assert fresh.lookup(agent_name="researcher", tool_name="Read") == "allow"


@pytest.mark.asyncio
async def test_always_allow_for_orchestrator_writes_disk(tmp_path: Path):
    bus = EventBus()
    inbox = PermissionInbox()
    rid = inbox.register(tool_name="Bash", tool_input={"cmd": "ls"})
    grants = PermissionGrants(cwd=tmp_path)

    app = _Host(bus=bus, inbox=inbox, grants=grants, agent_name="orchestrator")
    async with app.run_test() as pilot:
        await pilot.pause()
        bus.publish(_request(rid=rid, agent_name="orchestrator", tool="Bash"))
        await pilot.pause()
        # The scope-hint label should reference "the orchestrator".
        hint = app.screen.query_one("#scope-hint")
        assert "orchestrator" in str(hint.render()).lower()
        await pilot.click("#allow-always")
        await pilot.pause()
    fresh = PermissionGrants(cwd=tmp_path)
    assert fresh.lookup(agent_name="orchestrator", tool_name="Bash") == "allow"


@pytest.mark.asyncio
async def test_escape_denies_once(tmp_path: Path):
    from claude_agent_sdk import PermissionResultDeny
    bus = EventBus()
    inbox = PermissionInbox()
    rid = inbox.register(tool_name="Read", tool_input={})
    grants = PermissionGrants(cwd=tmp_path)

    app = _Host(bus=bus, inbox=inbox, grants=grants)
    async with app.run_test() as pilot:
        await pilot.pause()
        bus.publish(_request(rid=rid))
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
    fut = inbox._futures.get(rid)
    assert fut is None or (fut.done() and isinstance(fut.result(), PermissionResultDeny))


@pytest.mark.asyncio
async def test_modal_queues_second_request_until_first_resolves(tmp_path: Path):
    bus = EventBus()
    inbox = PermissionInbox()
    rid1 = inbox.register(tool_name="Read", tool_input={})
    rid2 = inbox.register(tool_name="Bash", tool_input={"cmd": "ls"})
    grants = PermissionGrants(cwd=tmp_path)

    app = _Host(bus=bus, inbox=inbox, grants=grants)
    async with app.run_test() as pilot:
        await pilot.pause()
        bus.publish(_request(rid=rid1))
        bus.publish(_request(rid=rid2))
        await pilot.pause()
        assert app.screen._current_request.request_id == rid1
        await pilot.click("#allow-once")
        await pilot.pause()
        assert app.screen._current_request.request_id == rid2


@pytest.mark.asyncio
async def test_modal_dismisses_when_last_request_resolved(tmp_path: Path):
    bus = EventBus()
    inbox = PermissionInbox()
    rid = inbox.register(tool_name="Read", tool_input={})
    grants = PermissionGrants(cwd=tmp_path)

    app = _Host(bus=bus, inbox=inbox, grants=grants)
    async with app.run_test() as pilot:
        await pilot.pause()
        bus.publish(_request(rid=rid))
        await pilot.pause()
        # Modal is up.
        assert isinstance(app.screen, PermissionModal)
        # Resolve the only request — modal should auto-dismiss.
        await pilot.click("#allow-once")
        await pilot.pause()
        assert not isinstance(app.screen, PermissionModal)
