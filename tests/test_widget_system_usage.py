"""Smoke + unit tests for the SystemUsage widget.

Pure-function tests cover the rendering helpers (bar drawing, color
selection, parsers). The mount test stubs the sample backend so no shell
calls happen and the test is fast and deterministic.
"""

from __future__ import annotations

import pytest
from textual.app import App

from patchfeld.widgets import system_usage as su
from patchfeld.widgets.system_usage import (
    SystemUsage,
    _bar,
    _color_for,
    _Sample,
)


def test_color_thresholds():
    assert _color_for(0) == "green"
    assert _color_for(49.9) == "green"
    assert _color_for(50) == "yellow"
    assert _color_for(79.9) == "yellow"
    assert _color_for(80) == "red"
    assert _color_for(100) == "red"


def test_bar_filled_count_matches_percentage():
    # 50% of width 12 → 6 filled cells.
    out = _bar(50.0, 12)
    assert out.count("▰") == 6
    assert out.count("▱") == 6


def test_bar_clamps_out_of_range():
    # Negative or >100 must not crash or overflow the cell budget.
    over = _bar(150.0, 8)
    under = _bar(-25.0, 8)
    assert over.count("▰") == 8 and over.count("▱") == 0
    assert under.count("▰") == 0 and under.count("▱") == 8


def test_bar_color_matches_threshold():
    assert "green" in _bar(10.0, 4)
    assert "yellow" in _bar(60.0, 4)
    assert "red" in _bar(95.0, 4)


def test_sample_ram_pct_handles_zero_total():
    s = _Sample(cpu_pct=10.0, ram_used_gib=0.0, ram_total_gib=0.0)
    # No /0 — return 0% rather than raising.
    assert s.ram_pct == 0.0


def test_sample_ram_pct_normal():
    s = _Sample(cpu_pct=10.0, ram_used_gib=4.0, ram_total_gib=16.0)
    assert s.ram_pct == 25.0


class _Host(App):
    def compose(self):
        yield SystemUsage(interval=0.25, bar_width=8)


@pytest.mark.asyncio
async def test_widget_renders_after_mount(monkeypatch):
    # Stub the sample backends so the test never shells out.
    async def _fake_sample() -> _Sample:
        return _Sample(cpu_pct=42.0, ram_used_gib=8.0, ram_total_gib=16.0)

    monkeypatch.setattr(su, "_sample_shellout", _fake_sample)
    monkeypatch.setattr(su, "_sample_psutil", _fake_sample)

    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        widget = app.query_one(SystemUsage)
        # Drive one tick directly so we don't depend on the timer firing.
        await widget._tick()
        await pilot.pause()
        # `Static.renderable` is the active content; coerce to str for the
        # assertions so we don't depend on rich.text vs plain-string typing.
        rendered = str(widget.render())
        assert "CPU" in rendered
        assert "42.0%" in rendered
        assert "RAM" in rendered
        assert "8.0/16.0 GiB" in rendered


@pytest.mark.asyncio
async def test_widget_clamps_props():
    # Out-of-range props must not break construction.
    w = SystemUsage(interval=0.0, bar_width=0)
    assert w._interval >= 0.25
    assert w._bar_width >= 2


@pytest.mark.asyncio
async def test_widget_surfaces_sample_errors_in_border_title(monkeypatch):
    async def _boom() -> _Sample:
        raise RuntimeError("kaboom")

    monkeypatch.setattr(su, "_sample_shellout", _boom)
    monkeypatch.setattr(su, "_sample_psutil", _boom)

    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        widget = app.query_one(SystemUsage)
        await widget._tick()
        await pilot.pause()
        # Error message goes to border_title; main content shows ?'s.
        assert "kaboom" in (widget.border_title or "")
        assert "?" in str(widget.render())


def test_registry_includes_system_usage():
    # Built-in widgets should be discoverable via the default registry.
    from patchfeld.app import build_default_registry

    reg = build_default_registry()
    info = reg.describe("SystemUsage")
    assert info is not None
    assert info.cls is SystemUsage
    assert "interval" in info.props_schema
    assert "bar_width" in info.props_schema
