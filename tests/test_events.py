from dataclasses import dataclass

from mod_tui.events import EventBus


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
