from dataclasses import dataclass

from patchfeld.events import EventBus


@dataclass
class Ping:
    msg: str


@dataclass
class Pong:
    n: int


def test_publish_with_no_subscribers_is_noop():
    bus = EventBus()
    bus.publish(Ping("hello"))  # must not raise


def test_subscriber_receives_published_event_of_matching_type():
    bus = EventBus()
    received: list[Ping] = []
    bus.subscribe(Ping, received.append)

    bus.publish(Ping("hi"))

    assert received == [Ping("hi")]


def test_subscriber_only_receives_events_of_subscribed_type():
    bus = EventBus()
    pings: list[Ping] = []
    pongs: list[Pong] = []
    bus.subscribe(Ping, pings.append)
    bus.subscribe(Pong, pongs.append)

    bus.publish(Ping("x"))
    bus.publish(Pong(3))

    assert pings == [Ping("x")]
    assert pongs == [Pong(3)]


def test_multiple_subscribers_each_receive_event():
    bus = EventBus()
    a, b = [], []
    bus.subscribe(Ping, a.append)
    bus.subscribe(Ping, b.append)

    bus.publish(Ping("y"))

    assert a == [Ping("y")] and b == [Ping("y")]


def test_unsubscribe_stops_delivery():
    bus = EventBus()
    received: list[Ping] = []
    unsub = bus.subscribe(Ping, received.append)

    bus.publish(Ping("first"))
    unsub()
    bus.publish(Ping("second"))

    assert received == [Ping("first")]


def test_handler_exception_does_not_break_other_handlers():
    bus = EventBus()
    good: list[Ping] = []

    def bad(_):
        raise RuntimeError("boom")

    bus.subscribe(Ping, bad)
    bus.subscribe(Ping, good.append)

    bus.publish(Ping("x"))  # must not raise

    assert good == [Ping("x")]


def test_unsubscribe_during_publish_does_not_skip_other_handlers():
    bus = EventBus()
    received_a: list[Ping] = []
    received_c: list[Ping] = []

    unsub_b: list = [None]

    def b(event):
        # B unsubscribes itself during dispatch.
        unsub_b[0]()

    bus.subscribe(Ping, received_a.append)
    unsub_b[0] = bus.subscribe(Ping, b)
    bus.subscribe(Ping, received_c.append)

    bus.publish(Ping("first"))
    bus.publish(Ping("second"))

    # A and C both receive both events; B's self-unsubscribe takes effect
    # AFTER the current publish finishes (snapshot semantics).
    assert received_a == [Ping("first"), Ping("second")]
    assert received_c == [Ping("first"), Ping("second")]


def test_agent_message_appended_has_optional_tool_fields():
    from patchfeld.events import AgentMessageAppended

    # Backwards-compatible default — old call sites still work.
    e1 = AgentMessageAppended(agent_id="a", role="assistant", text="hi")
    assert e1.tool_id is None
    assert e1.tool_name is None

    # New call sites can carry SDK-provided ids.
    e2 = AgentMessageAppended(
        agent_id="a", role="tool_use", text="...",
        tool_id="toolu_abc", tool_name="bash",
    )
    assert e2.tool_id == "toolu_abc"
    assert e2.tool_name == "bash"


from patchfeld.events import (
    LayoutApplied,
    LayoutFailed,
    TabAdded,
    TabClosed,
    TabSwitched,
)


def test_tab_added_event_has_id_and_title():
    e = TabAdded(tab_id="t1", title="Main")
    assert e.tab_id == "t1"
    assert e.title == "Main"


def test_tab_closed_event_has_id():
    e = TabClosed(tab_id="t1")
    assert e.tab_id == "t1"


def test_tab_switched_event_has_id_and_title():
    e = TabSwitched(tab_id="t1", title="Main")
    assert (e.tab_id, e.title) == ("t1", "Main")


def test_layout_applied_includes_tab_id():
    from patchfeld.layout.spec import LayoutSpec
    spec = LayoutSpec.model_validate({
        "version": 1,
        "layout": {"id": "orch", "widget": "OrchestratorChat"},
    })
    e = LayoutApplied(spec=spec, layout_name=None, tab_id="t1")
    assert e.tab_id == "t1"


def test_layout_applied_tab_id_defaults_to_none():
    from patchfeld.layout.spec import LayoutSpec
    spec = LayoutSpec.model_validate({
        "version": 1,
        "layout": {"id": "orch", "widget": "OrchestratorChat"},
    })
    e = LayoutApplied(spec=spec)
    assert e.tab_id is None


def test_layout_failed_includes_tab_id():
    e = LayoutFailed(error="boom", tab_id="t1")
    assert e.tab_id == "t1"
    e2 = LayoutFailed(error="boom")
    assert e2.tab_id is None


def test_orchestrator_session_switched_event_carries_id_and_path():
    from patchfeld.events import OrchestratorSessionSwitched
    e = OrchestratorSessionSwitched(session_id="abc", transcript_path="/tmp/x.jsonl")
    assert e.session_id == "abc"
    assert e.transcript_path == "/tmp/x.jsonl"


def test_open_resume_picker_event_is_constructible():
    from patchfeld.events import OpenResumePicker
    OpenResumePicker()  # smoke


def test_keyed_subscribe_only_receives_events_for_matching_agent_id():
    """A subscriber that opts into agent_id keying must NOT see events from
    other agents. With many agents mounted this avoids N-way fanout where
    every subscriber re-checks event.agent_id itself."""
    from patchfeld.events import AgentMessageAppended

    bus = EventBus()
    a1, a2 = [], []
    bus.subscribe(AgentMessageAppended, a1.append, agent_id="a1")
    bus.subscribe(AgentMessageAppended, a2.append, agent_id="a2")

    bus.publish(AgentMessageAppended(agent_id="a1", role="assistant", text="hi"))
    bus.publish(AgentMessageAppended(agent_id="a2", role="assistant", text="ho"))
    bus.publish(AgentMessageAppended(agent_id="a3", role="assistant", text="ignored"))

    assert [e.text for e in a1] == ["hi"]
    assert [e.text for e in a2] == ["ho"]


def test_typewide_subscribers_still_receive_all_events_after_keyed_subs_added():
    """Adding keyed subscribers must not change behavior for type-only
    subscribers — they still see every event, regardless of agent_id."""
    from patchfeld.events import AgentMessageAppended

    bus = EventBus()
    type_wide: list[AgentMessageAppended] = []
    keyed_a1: list[AgentMessageAppended] = []

    bus.subscribe(AgentMessageAppended, type_wide.append)  # no agent_id
    bus.subscribe(AgentMessageAppended, keyed_a1.append, agent_id="a1")

    bus.publish(AgentMessageAppended(agent_id="a1", role="assistant", text="x"))
    bus.publish(AgentMessageAppended(agent_id="a2", role="assistant", text="y"))

    assert [e.agent_id for e in type_wide] == ["a1", "a2"]
    assert [e.agent_id for e in keyed_a1] == ["a1"]


def test_keyed_subscribe_unsubscribe_works():
    """The returned Unsubscribe callable removes a keyed subscription
    without affecting other keys or type-wide subscribers."""
    from patchfeld.events import AgentMessageAppended

    bus = EventBus()
    received: list[AgentMessageAppended] = []
    unsub = bus.subscribe(AgentMessageAppended, received.append, agent_id="a1")

    bus.publish(AgentMessageAppended(agent_id="a1", role="assistant", text="first"))
    unsub()
    bus.publish(AgentMessageAppended(agent_id="a1", role="assistant", text="second"))

    assert [e.text for e in received] == ["first"]


def test_keyed_subscribers_handle_event_with_no_agent_id_field():
    """If the event class has no agent_id (e.g. StatsUpdated), keyed
    subscribers are simply not eligible — only type-wide subs receive it.
    This guards against accidentally dispatching the wrong shape of event."""
    from patchfeld.events import StatsUpdated

    bus = EventBus()
    type_wide: list[StatsUpdated] = []
    keyed: list[StatsUpdated] = []
    bus.subscribe(StatsUpdated, type_wide.append)
    bus.subscribe(StatsUpdated, keyed.append, agent_id="a1")

    bus.publish(StatsUpdated(tokens_in=1))

    assert len(type_wide) == 1
    assert keyed == []


def test_permission_request_events_carry_required_fields():
    from patchfeld.events import PermissionRequested, PermissionResolved
    req = PermissionRequested(
        agent_id="a1", agent_name="researcher",
        request_id="r1", tool_name="Read", tool_input={"path": "x"},
        title=None, description=None,
    )
    assert req.agent_id == "a1"
    assert req.tool_name == "Read"

    res = PermissionResolved(
        agent_id="a1", request_id="r1", behavior="allow",
    )
    assert res.behavior == "allow"
