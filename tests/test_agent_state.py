from mod_tui.agents.state import AgentInfo, AgentState


def test_agent_state_values():
    assert AgentState.IDLE.value == "idle"
    assert AgentState.RUNNING.value == "running"
    assert AgentState.WAITING.value == "waiting"
    assert AgentState.DONE.value == "done"
    assert AgentState.ERROR.value == "error"


def test_agent_state_terminal():
    assert AgentState.DONE.is_terminal
    assert AgentState.ERROR.is_terminal
    assert not AgentState.IDLE.is_terminal
    assert not AgentState.RUNNING.is_terminal
    assert not AgentState.WAITING.is_terminal


def test_agent_info_defaults():
    info = AgentInfo(id="abc", name="research", cwd="/tmp", started_at=1700000000.0)
    assert info.state == AgentState.IDLE
    assert info.ended_at is None
    assert info.last_activity == info.started_at
    assert info.cost == 0.0
    assert info.tokens_in == 0
    assert info.tokens_out == 0


def test_agent_info_elapsed_seconds():
    info = AgentInfo(id="x", name="y", cwd="/tmp", started_at=100.0)
    info.last_activity = 130.0
    assert info.elapsed_seconds() == 30.0


def test_agent_info_round_trip_dict():
    info = AgentInfo(
        id="abc", name="research", cwd="/tmp", started_at=100.0,
        state=AgentState.DONE, ended_at=200.0, last_activity=199.0,
        cost=0.123, tokens_in=500, tokens_out=750,
    )
    d = info.to_dict()
    again = AgentInfo.from_dict(d)
    assert again == info
