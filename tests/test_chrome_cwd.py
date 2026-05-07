from pathlib import Path

import pytest

from mod_tui.widgets.chrome import _format_cwd


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
