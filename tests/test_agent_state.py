from patchfeld.agents.state import AgentInfo, AgentState


def test_agent_state_values():
    assert AgentState.IDLE.value == "idle"
    assert AgentState.RUNNING.value == "running"
    assert AgentState.WAITING.value == "waiting"
    assert AgentState.AWAITING_PERMISSION.value == "awaiting_permission"
    assert AgentState.DONE.value == "done"
    assert AgentState.ERROR.value == "error"


def test_agent_state_terminal():
    assert AgentState.DONE.is_terminal
    assert AgentState.ERROR.is_terminal
    assert not AgentState.IDLE.is_terminal
    assert not AgentState.RUNNING.is_terminal
    assert not AgentState.WAITING.is_terminal
    assert not AgentState.AWAITING_PERMISSION.is_terminal


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
        session_id="sdk-session-xyz",
        spawn_options={"cwd": "/tmp", "model": "claude-sonnet-4-6"},
    )
    d = info.to_dict()
    again = AgentInfo.from_dict(d)
    assert again == info


def test_agent_info_archived_defaults_to_false():
    info = AgentInfo(id="abc", name="research", cwd="/tmp", started_at=100.0)
    assert info.archived is False


def test_agent_info_archived_round_trips_through_dict():
    info = AgentInfo(
        id="abc", name="research", cwd="/tmp", started_at=100.0,
        archived=True,
    )
    again = AgentInfo.from_dict(info.to_dict())
    assert again.archived is True
    # And explicit False survives a round trip too.
    info2 = AgentInfo(id="x", name="y", cwd="/tmp", started_at=1.0)
    assert AgentInfo.from_dict(info2.to_dict()).archived is False


def test_agent_info_archived_back_compat_when_missing_from_dict():
    # Older agents.json files won't have the "archived" key — they should
    # load as not-archived rather than blowing up.
    legacy = {
        "id": "abc", "name": "research", "cwd": "/tmp", "started_at": 1.0,
        "state": "idle", "ended_at": None, "last_activity": 1.0,
        "cost": 0.0, "tokens_in": 0, "tokens_out": 0,
    }
    assert AgentInfo.from_dict(legacy).archived is False


def test_agent_info_from_dict_tolerates_legacy_records():
    # Records written before the resume feature lack session_id and
    # spawn_options. from_dict must read them as None, not crash on KeyError.
    legacy = {
        "id": "old", "name": "agent", "cwd": "/tmp", "started_at": 100.0,
        "state": "done", "ended_at": 110.0, "last_activity": 109.0,
        "cost": 0.0, "tokens_in": 0, "tokens_out": 0,
    }
    info = AgentInfo.from_dict(legacy)
    assert info.session_id is None
    assert info.spawn_options is None


def test_awaiting_permission_is_a_distinct_state():
    assert AgentState.AWAITING_PERMISSION.value == "awaiting_permission"
    assert AgentState.AWAITING_PERMISSION != AgentState.WAITING


def test_awaiting_permission_is_not_terminal():
    assert AgentState.AWAITING_PERMISSION.is_terminal is False
