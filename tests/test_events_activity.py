from patchfeld.events import ActivityLogged, AgentFocusRequested


def test_activity_logged_carries_entry():
    sentinel = object()
    e = ActivityLogged(entry=sentinel)
    assert e.entry is sentinel


def test_agent_focus_requested_carries_id():
    e = AgentFocusRequested(agent_id="abc123")
    assert e.agent_id == "abc123"


def test_events_are_frozen():
    import dataclasses
    e = ActivityLogged(entry=None)
    assert dataclasses.is_dataclass(e) and dataclasses.fields(e)
    try:
        e.entry = 1  # type: ignore[misc]
    except dataclasses.FrozenInstanceError:
        return
    raise AssertionError("ActivityLogged must be frozen")
