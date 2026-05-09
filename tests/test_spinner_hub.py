"""Tier 1.4 — unified spinner ticker.

Multiple Collapsible spinners (one per running tool call, plus one for
each thinking group, plus the outer process group) used to each run
their own 12.5 Hz set_interval. With three concurrent spinners the
title-rebuild + reassign hit happened ~37 times per second on the
Textual main loop. The SpinnerHub coalesces them onto a single timer.
"""
from __future__ import annotations


class _FakeTimer:
    def __init__(self, fn):
        self.fn = fn
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


class _FakeApp:
    def __init__(self) -> None:
        self.starts = 0

    def set_interval(self, interval, fn):  # noqa: ARG002 — match Textual signature
        self.starts += 1
        return _FakeTimer(fn)


class _FakeWidget:
    def __init__(self, app: _FakeApp) -> None:
        self.app = app


def test_hub_starts_one_timer_for_many_subscribers():
    from patchfeld.widgets.rich_transcript import _SpinnerHub

    app = _FakeApp()
    hub = _SpinnerHub()
    cb1, cb2, cb3 = (lambda: None for _ in range(3))

    hub.subscribe(_FakeWidget(app), cb1)
    hub.subscribe(_FakeWidget(app), cb2)
    hub.subscribe(_FakeWidget(app), cb3)

    assert app.starts == 1, "subsequent subscribers must reuse the timer"


def test_hub_stops_timer_when_last_subscriber_leaves():
    from patchfeld.widgets.rich_transcript import _SpinnerHub

    app = _FakeApp()
    hub = _SpinnerHub()
    cb1, cb2 = (lambda: None for _ in range(2))

    hub.subscribe(_FakeWidget(app), cb1)
    hub.subscribe(_FakeWidget(app), cb2)
    timer = hub._timer  # noqa: SLF001
    assert isinstance(timer, _FakeTimer)

    hub.unsubscribe(cb1)
    assert hub._timer is timer  # noqa: SLF001 — still spinning for cb2

    hub.unsubscribe(cb2)
    assert hub._timer is None  # noqa: SLF001
    assert timer.stopped is True


def test_hub_tick_advances_phase_and_notifies_subscribers():
    from patchfeld.widgets.rich_transcript import _SPINNER_FRAMES, _SpinnerHub

    app = _FakeApp()
    hub = _SpinnerHub()
    calls = {"a": 0, "b": 0}
    hub.subscribe(_FakeWidget(app), lambda: calls.__setitem__("a", calls["a"] + 1))
    hub.subscribe(_FakeWidget(app), lambda: calls.__setitem__("b", calls["b"] + 1))

    initial = hub.phase
    hub._tick()  # noqa: SLF001
    hub._tick()  # noqa: SLF001
    hub._tick()  # noqa: SLF001

    assert hub.phase == (initial + 3) % len(_SPINNER_FRAMES)
    assert calls == {"a": 3, "b": 3}


def test_hub_subscriber_exception_does_not_break_others():
    from patchfeld.widgets.rich_transcript import _SpinnerHub

    app = _FakeApp()
    hub = _SpinnerHub()
    other_calls = 0

    def boom() -> None:
        raise RuntimeError("boom")

    def good() -> None:
        nonlocal other_calls
        other_calls += 1

    hub.subscribe(_FakeWidget(app), boom)
    hub.subscribe(_FakeWidget(app), good)

    hub._tick()  # noqa: SLF001 — should not raise

    assert other_calls == 1
