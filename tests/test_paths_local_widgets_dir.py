from pathlib import Path

from patchbai.persistence.paths import local_widgets_dir


def test_local_widgets_dir_under_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert local_widgets_dir() == tmp_path / "patchbai" / "widgets"


def test_local_widgets_dir_default(monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert local_widgets_dir() == tmp_path / ".config" / "patchbai" / "widgets"


def test_local_widgets_dir_explicit_global_dir(tmp_path):
    assert local_widgets_dir(global_dir=tmp_path) == tmp_path / "widgets"
