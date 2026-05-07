"""Tests for the StatusBar's persistent shortcut hint widget (`sb-hints`).

The bottom-right of the footer surfaces the two most basic keybindings —
`?` to open help and `Ctrl+Q` to quit — so a brand-new user never has to
guess how to get out or get help. These tests pin down the contract:

1. ``StatusBar.compose()`` yields a Static with id ``sb-hints``.
2. The hint text references both `?` and the actual quit shortcut.
3. After mount, the widget has non-empty rendered content.
4. The hint sits at the rightmost edge of the bar (not jammed next to
   sb-error in the middle).
"""
from pathlib import Path

import pytest
from textual.widgets import Static

from patchbai.app import PatchbaiApp
from patchbai.widgets.chrome import StatusBar


def test_status_bar_compose_yields_sb_hints_static():
    """compose() yields exactly one Static with id 'sb-hints'."""
    bar = StatusBar()
    yielded = list(bar.compose())
    ids = [getattr(w, "id", None) for w in yielded]
    assert ids.count("sb-hints") == 1, (
        f"expected exactly one Static with id 'sb-hints', got ids={ids}"
    )
    hints = next(w for w in yielded if getattr(w, "id", None) == "sb-hints")
    assert isinstance(hints, Static)


@pytest.mark.asyncio
async def test_sb_hints_references_help_and_quit_shortcuts(tmp_path, monkeypatch):
    """After mount, the rendered text references both the `?` help binding
    and the `^Q` quit binding so a new user can always see how to escape."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    project = tmp_path / "proj"
    project.mkdir()
    app = PatchbaiApp(cwd=project, global_dir=tmp_path / "cfg")
    async with app.run_test(size=(140, 30)) as pilot:
        await pilot.pause()
        bar = app.query_one(StatusBar)
        hints = bar.query_one("#sb-hints", Static)
        text = str(hints.content)
        assert text.strip(), f"sb-hints rendered empty after mount: {text!r}"
        # `?` is the App-level help binding (see PatchbaiApp.BINDINGS).
        assert "?" in text, f"hint text missing '?': {text!r}"
        # `^Q` is the conventional monospace rendering of ctrl+q.
        assert "^Q" in text, f"hint text missing '^Q': {text!r}"


@pytest.mark.asyncio
async def test_sb_hints_is_right_aligned_against_bar_edge(tmp_path, monkeypatch):
    """sb-hints must hug the right edge — its right edge sits at the bar's
    right edge (within 1 col of slack). Otherwise the hint would float in
    the middle next to sb-error and look like just another widget."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    project = tmp_path / "proj"
    project.mkdir()
    app = PatchbaiApp(cwd=project, global_dir=tmp_path / "cfg")
    async with app.run_test(size=(140, 30)) as pilot:
        await pilot.pause()
        bar = app.query_one(StatusBar)
        hints = bar.query_one("#sb-hints", Static)
        bar_right = bar.region.x + bar.region.width
        hints_right = hints.region.x + hints.region.width
        assert hints_right >= bar_right - 1, (
            f"sb-hints is not flush right: hints_right={hints_right}, "
            f"bar_right={bar_right}"
        )
        # And it must sit to the right of sb-error (not before it).
        err = bar.query_one("#sb-error", Static)
        assert hints.region.x >= err.region.x + err.region.width
