from mod_tui.agents.state import AgentInfo, AgentState
from mod_tui.events import (
    AgentMessageAppended,
    AgentSpawned,
    AgentStateChanged,
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
