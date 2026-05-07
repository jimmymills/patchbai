import asyncio

import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

from mod_tui.agents.fake_sdk_adapter import FakeSDKAdapter
from mod_tui.agents.manager import AgentManager
from mod_tui.agents.state import AgentState
from mod_tui.events import (
    AgentRequestedUserInput,
    AgentStateChanged,
    EventBus,
    UserMessageToOrchestrator,
)
from mod_tui.orchestrator.session import OrchestratorSession
from mod_tui.orchestrator.tools import build_orchestrator_tools


def _ok():
    return [
        AssistantMessage(content=[TextBlock(text="done")], model="fake-model"),
        ResultMessage(
            subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
            num_turns=1, session_id="fake", total_cost_usd=0.0,
            usage={"input_tokens": 1, "output_tokens": 1}, result="done",
        ),
    ]


@pytest.mark.asyncio
async def test_ask_orchestrator_round_trip(tmp_path):
    bus = EventBus()
    user_messages: list[UserMessageToOrchestrator] = []
    bus.subscribe(UserMessageToOrchestrator, user_messages.append)

    manager = AgentManager(
        cwd=tmp_path,
        bus=bus,
        adapter_factory=lambda: FakeSDKAdapter(scripts=[_ok()]),
    )
    orchestrator = OrchestratorSession(
        cwd=tmp_path,
        bus=bus,
        manager=manager,
        adapter=FakeSDKAdapter(scripts=[_ok()]),
    )
    await orchestrator.start()

    state_events: list[AgentStateChanged] = []
    bus.subscribe(AgentStateChanged, state_events.append)

    aid = await manager.spawn(name="alpha", prompt="say hi")
    await manager.wait_idle(aid)

    inbox = manager.get_inbox(aid)
    # Coerce the session out of DONE (the canned script ended the stream
    # already) so the inbox-driven WAITING transition is visible.
    manager.get_session(aid).info.state = AgentState.RUNNING
    request_id = inbox.register()
    bus.publish(
        AgentRequestedUserInput(
            agent_id=aid, question="go/no-go?", request_id=request_id
        )
    )
    await asyncio.sleep(0)

    # The orchestrator session injected a synthetic user message describing
    # the question.
    assert any("go/no-go" in m.text for m in user_messages)
    assert any(request_id in m.text for m in user_messages)

    # The orchestrator (in production: the AI) calls respond_to_agent_request.
    # We invoke it directly here.
    tools = build_orchestrator_tools(manager)
    respond = tools["respond_to_agent_request"]

    # Race the wait against the resolution.
    async def waiter():
        return await inbox.wait(request_id, timeout_s=1.0)
    waiter_task = asyncio.create_task(waiter())

    await respond({"agent_id": aid, "request_id": request_id, "response": "ship it"})

    answer = await waiter_task
    assert answer == "ship it"

    pairs = [(e.old_state, e.info.state) for e in state_events if e.info.id == aid]
    assert (AgentState.RUNNING, AgentState.WAITING) in pairs, \
        f"expected RUNNING → WAITING, got {pairs}"
    assert (AgentState.WAITING, AgentState.RUNNING) in pairs, \
        f"expected WAITING → RUNNING, got {pairs}"

    await orchestrator.stop()
