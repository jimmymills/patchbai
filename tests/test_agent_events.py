from patchbai.agents.state import AgentInfo, AgentState
from patchbai.events import (
    AgentMessageAppended,
    AgentNotifiedOrchestrator,
    AgentSpawned,
    AgentStateChanged,
    DirectMessageToAgent,
    EventBus,
)


def _info(state: AgentState = AgentState.IDLE) -> AgentInfo:
    return AgentInfo(id="a1", name="research", cwd="/tmp", started_at=100.0, state=state)


def test_agent_spawned_routes_to_subscriber():
    bus = EventBus()
    received: list[AgentSpawned] = []
    bus.subscribe(AgentSpawned, received.append)

    bus.publish(AgentSpawned(info=_info()))

    assert len(received) == 1
    assert received[0].info.id == "a1"


def test_agent_state_changed_carries_old_and_new():
    bus = EventBus()
    received: list[AgentStateChanged] = []
    bus.subscribe(AgentStateChanged, received.append)

    bus.publish(AgentStateChanged(
        info=_info(state=AgentState.RUNNING),
        old_state=AgentState.IDLE,
    ))

    assert received[0].old_state == AgentState.IDLE
    assert received[0].info.state == AgentState.RUNNING


def test_agent_message_appended_carries_role_and_text():
    bus = EventBus()
    received: list[AgentMessageAppended] = []
    bus.subscribe(AgentMessageAppended, received.append)

    bus.publish(AgentMessageAppended(
        agent_id="a1",
        role="assistant",
        text="hello world",
    ))

    assert received[0].agent_id == "a1"
    assert received[0].role == "assistant"
    assert received[0].text == "hello world"


def test_agent_notified_orchestrator_carries_text():
    bus = EventBus()
    received: list[AgentNotifiedOrchestrator] = []
    bus.subscribe(AgentNotifiedOrchestrator, received.append)

    bus.publish(AgentNotifiedOrchestrator(agent_id="a1", message="task complete"))
    assert received[0].agent_id == "a1"
    assert received[0].message == "task complete"


def test_direct_message_to_agent_carries_text():
    bus = EventBus()
    received: list[DirectMessageToAgent] = []
    bus.subscribe(DirectMessageToAgent, received.append)

    bus.publish(DirectMessageToAgent(agent_id="a1", text="hi from user"))
    assert received[0].agent_id == "a1"
    assert received[0].text == "hi from user"
