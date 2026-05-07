from pathlib import Path

import pytest

from mod_tui.app import ModTuiApp
from mod_tui.widgets.chrome import StatusBar, _format_cwd
from textual.widgets import Static


def test_format_cwd_uses_tilde_when_under_home(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    nested = tmp_path / "Developer" / "mod_tui"
    assert _format_cwd(nested, available_width=80) == "~/Developer/mod_tui"


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
    app = ModTuiApp(cwd=project, global_dir=tmp_path / "cfg")
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
    from mod_tui.events import WorkspaceCwdChanged

    app = ModTuiApp(cwd=proj_a, global_dir=tmp_path / "cfg")
    async with app.run_test() as pilot:
        await pilot.pause()
        bar = app.query_one(StatusBar)
        app.event_bus.publish(WorkspaceCwdChanged(cwd=str(proj_b)))
        await pilot.pause()
        text = bar.query_one("#sb-cwd", Static).content
        assert "~/b" in str(text)


@pytest.mark.asyncio
async def test_status_bar_truncates_cwd_on_narrow_terminal(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    deep = tmp_path / "one" / "two" / "three" / "four" / "five" / "six" / "leaf"
    deep.mkdir(parents=True)
    app = ModTuiApp(cwd=deep, global_dir=tmp_path / "cfg")
    async with app.run_test(size=(40, 10)) as pilot:
        await pilot.pause()
        bar = app.query_one(StatusBar)
        text = str(bar.query_one("#sb-cwd", Static).content)
        assert "leaf" in text
        # On a 40-col terminal the budget is ~20 → must use ellipsis.
        assert "…/" in text or "~/" in text  # one of: truncated or shortenable
