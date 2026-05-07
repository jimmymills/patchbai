from patchbai.agents.sort import sort_agents
from patchbai.agents.state import AgentInfo, AgentState


def _info(
    id: str,
    state: AgentState = AgentState.RUNNING,
    *,
    started_at: float = 100.0,
    last_activity: float | None = None,
    ended_at: float | None = None,
    archived: bool = False,
) -> AgentInfo:
    return AgentInfo(
        id=id,
        name=f"agent-{id}",
        cwd="/tmp",
        started_at=started_at,
        state=state,
        last_activity=last_activity if last_activity is not None else started_at,
        ended_at=ended_at,
        archived=archived,
    )


def test_state_priority_waiting_running_idle_error_done():
    infos = [
        _info("d", state=AgentState.DONE),
        _info("e", state=AgentState.ERROR),
        _info("i", state=AgentState.IDLE),
        _info("r", state=AgentState.RUNNING),
        _info("w", state=AgentState.WAITING),
    ]
    ordered = [i.id for i in sort_agents(infos)]
    assert ordered == ["w", "r", "i", "e", "d"]


def test_within_bucket_last_activity_desc():
    infos = [
        _info("old", state=AgentState.RUNNING, started_at=100.0, last_activity=110.0),
        _info("new", state=AgentState.RUNNING, started_at=100.0, last_activity=200.0),
        _info("mid", state=AgentState.RUNNING, started_at=100.0, last_activity=150.0),
    ]
    ordered = [i.id for i in sort_agents(infos)]
    assert ordered == ["new", "mid", "old"]


def test_done_bucket_uses_ended_at_when_present():
    # Both DONE; the one that ended later sorts first even though its
    # last_activity is older — ended_at is the canonical "finished at".
    a = _info("late", state=AgentState.DONE, last_activity=100.0, ended_at=200.0)
    b = _info("early", state=AgentState.DONE, last_activity=150.0, ended_at=180.0)
    ordered = [i.id for i in sort_agents([b, a])]
    assert ordered == ["late", "early"]


def test_done_bucket_falls_back_to_last_activity_when_ended_at_missing():
    a = _info("a", state=AgentState.DONE, last_activity=200.0, ended_at=None)
    b = _info("b", state=AgentState.DONE, last_activity=100.0, ended_at=None)
    ordered = [i.id for i in sort_agents([b, a])]
    assert ordered == ["a", "b"]


def test_started_at_breaks_full_ties():
    a = _info("first", state=AgentState.RUNNING, started_at=100.0, last_activity=200.0)
    b = _info("second", state=AgentState.RUNNING, started_at=150.0, last_activity=200.0)
    ordered = [i.id for i in sort_agents([b, a])]
    assert ordered == ["first", "second"]


def test_archived_sinks_below_every_live_row():
    infos = [
        _info("archived-running", state=AgentState.RUNNING, archived=True,
              last_activity=999.0),  # very recent — would otherwise top the table
        _info("live-done", state=AgentState.DONE, last_activity=100.0,
              ended_at=100.0),
        _info("live-waiting", state=AgentState.WAITING, last_activity=50.0),
    ]
    ordered = [i.id for i in sort_agents(infos)]
    assert ordered == ["live-waiting", "live-done", "archived-running"]


def test_archived_among_themselves_order_by_ended_at_desc():
    infos = [
        _info("a", state=AgentState.DONE, archived=True, ended_at=100.0),
        _info("b", state=AgentState.DONE, archived=True, ended_at=200.0),
        _info("c", state=AgentState.ERROR, archived=True, ended_at=150.0),
    ]
    ordered = [i.id for i in sort_agents(infos)]
    assert ordered == ["b", "c", "a"]


def test_empty_input():
    assert sort_agents([]) == []


def test_single_agent():
    info = _info("only")
    assert sort_agents([info]) == [info]


def test_just_spawned_agent_has_usable_timestamp():
    # AgentInfo.__post_init__ defaults last_activity to started_at, so
    # the sort key never sees a 0.0/None last_activity for a new agent.
    info = _info("new", state=AgentState.RUNNING, started_at=500.0)
    assert info.last_activity == 500.0
    assert sort_agents([info]) == [info]
