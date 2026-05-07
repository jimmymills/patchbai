import asyncio

import pytest

from patchbai.agents.fake_sdk_adapter import FakeSDKAdapter
from patchbai.agents.manager import AgentManager
from patchbai.events import EventBus, UserMessageToOrchestrator
from patchbai.orchestrator.session import OrchestratorSession


@pytest.mark.asyncio
async def test_send_tasks_does_not_grow_unboundedly(tmp_path, ok_script):
    bus = EventBus()
    manager = AgentManager(
        cwd=tmp_path,
        bus=bus,
        adapter_factory=lambda: FakeSDKAdapter(scripts=[ok_script()]),
    )
    scripts = [ok_script(f"reply {i}") for i in range(5)]
    session = OrchestratorSession(
        cwd=tmp_path,
        bus=bus,
        manager=manager,
        adapter=FakeSDKAdapter(scripts=scripts),
    )
    await session.start()

    for i in range(5):
        bus.publish(UserMessageToOrchestrator(f"msg {i}"))
        await session.wait_idle()
        # Done tasks must be pruned on each new send. After the loop
        # iteration, _send_tasks should hold at most a small constant
        # number of entries (the just-completed one plus zero pending).
        assert len(session._send_tasks) <= 2, (
            f"send_tasks grew to {len(session._send_tasks)} — pruning failed"
        )

    # After the final wait_idle, no live (not-done) tasks should remain.
    live = [t for t in session._send_tasks if not t.done()]
    assert live == []
