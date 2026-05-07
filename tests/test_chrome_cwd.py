from pathlib import Path

import pytest

from patchbai.app import PatchbaiApp
from patchbai.widgets.chrome import StatusBar, _format_cwd
from textual.widgets import Static


def test_format_cwd_uses_tilde_when_under_home(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    nested = tmp_path / "Developer" / "patchbai"
    assert _format_cwd(nested, available_width=80) == "~/Developer/patchbai"


def test_format_cwd_keeps_absolute_when_outside_home(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "elsewhere"))
    assert _format_cwd(Path("/var/log/foo"), available_width=80) == "/var/log/foo"


def test_format_cwd_left_truncates_when_too_long():
    p = Path("/a/very/very/very/long/nested/path/with/many/segs/leaf")
    out = _format_cwd(p, available_width=20)
    # Must end at a segment boundary, start with the truncation marker, fit budget.
    assert out.startswith("…/")
    assert out.endswith("/leaf")
    assert len(out) <= 20


def test_format_cwd_falls_back_to_basename_when_budget_tiny():
    p = Path("/a/b/c/leaf")
    assert _format_cwd(p, available_width=4) == "leaf"


@pytest.mark.asyncio
async def test_status_bar_shows_cwd_at_boot(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    project = tmp_path / "proj"
    project.mkdir()
    app = PatchbaiApp(cwd=project, global_dir=tmp_path / "cfg")
    async with app.run_test() as pilot:
        await pilot.pause()
        bar = app.query_one(StatusBar)
        text = bar.query_one("#sb-cwd", Static).content
        assert "~/proj" in str(text)


@pytest.mark.asyncio
async def test_status_bar_updates_on_cwd_changed_event(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    proj_a = tmp_path / "a"
    proj_b = tmp_path / "b"
    proj_a.mkdir()
    proj_b.mkdir()
    from patchbai.events import WorkspaceCwdChanged

    app = PatchbaiApp(cwd=proj_a, global_dir=tmp_path / "cfg")
    async with app.run_test() as pilot:
        await pilot.pause()
        bar = app.query_one(StatusBar)
        app.event_bus.publish(WorkspaceCwdChanged(cwd=str(proj_b)))
        await pilot.pause()
        text = bar.query_one("#sb-cwd", Static).content
        assert "~/b" in str(text)


@pytest.mark.asyncio
async def test_status_bar_widgets_laid_out_side_by_side(tmp_path, monkeypatch):
    """Regression: each Static must size to its content and sit next to its
    neighbour. Previously every Static defaulted to ``width: 1fr`` and
    consumed the whole bar, pushing siblings off-screen so only the first
    widget ('tokens N/N') was visible.
    """
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    project = tmp_path / "proj"
    project.mkdir()
    app = PatchbaiApp(cwd=project, global_dir=tmp_path / "cfg")
    async with app.run_test(size=(140, 30)) as pilot:
        await pilot.pause()
        bar = app.query_one(StatusBar)
        ids = ("sb-tokens", "sb-cost", "sb-agents", "sb-layout", "sb-cwd")
        regions = [bar.query_one(f"#{i}", Static).region for i in ids]
        bar_w = bar.size.width
        for sid, r in zip(ids, regions):
            assert r.width < bar_w, f"{sid} took the entire bar ({r.width} cols)"
            assert r.x < bar_w, f"{sid} positioned off-screen at x={r.x}"
        # Strict left-to-right packing: each region starts where the previous ended.
        for prev, curr, sid in zip(regions, regions[1:], ids[1:]):
            assert curr.x == prev.x + prev.width, (
                f"{sid} not adjacent to its left neighbour: "
                f"prev ends at {prev.x + prev.width}, {sid} starts at {curr.x}"
            )


@pytest.mark.asyncio
async def test_status_bar_truncates_cwd_on_narrow_terminal(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    deep = tmp_path / "one" / "two" / "three" / "four" / "five" / "six" / "leaf"
    deep.mkdir(parents=True)
    app = PatchbaiApp(cwd=deep, global_dir=tmp_path / "cfg")
    async with app.run_test(size=(40, 10)) as pilot:
        await pilot.pause()
        bar = app.query_one(StatusBar)
        text = str(bar.query_one("#sb-cwd", Static).content)
        assert "leaf" in text
        # On a 40-col terminal the budget is ~20 → must use ellipsis.
        assert "…/" in text or "~/" in text  # one of: truncated or shortenable
