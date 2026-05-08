import os

import pytest

from patchfeld.agents.manager import AgentManager
from patchfeld.agents.sdk_adapter import RealSDKAdapter
from patchfeld.events import EventBus


pytestmark = pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="Requires ANTHROPIC_API_KEY for live SDK smoke test.",
)


@pytest.mark.asyncio
async def test_real_child_agent_completes(tmp_path):
    """Live test: spawn a child agent and let it run a single turn.

    Skipped unless ANTHROPIC_API_KEY is set. Costs a fraction of a cent."""
    bus = EventBus()
    manager = AgentManager(cwd=tmp_path, bus=bus, adapter_factory=RealSDKAdapter)
    agent_id = await manager.spawn(
        name="smoke",
        prompt="Reply with just the word 'hello' and nothing else.",
        allowed_tools=[],  # no tools needed; just text response
    )
    await manager.wait_idle(agent_id)

    info = next(i for i in manager.list_infos() if i.id == agent_id)
    assert info.state.is_terminal, f"agent did not reach terminal state: {info.state}"
    entries = manager.read_transcript(agent_id)
    assert any(e.role == "assistant" for e in entries)
    assert info.tokens_in > 0
    assert info.tokens_out > 0

    await manager.shutdown()
