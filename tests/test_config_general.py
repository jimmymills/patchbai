import pytest

from patchbai.config import Config, ConfigStore


def test_get_path_works_for_unknown_section_after_extension(tmp_path, monkeypatch):
    """If we add a new section to Config, get_path/set_path should handle it
    without modifying their bodies. We monkeypatch a 'logs' section onto a
    Config instance to simulate a future addition."""
    cfg = Config()

    class _LogsSection:
        level = "info"
        path = "/var/log/patchbai.log"

    cfg.logs = _LogsSection()  # type: ignore[attr-defined]

    assert cfg.get_path("logs.level") == "info"
    cfg.set_path("logs.level", "debug")
    assert cfg.get_path("logs.level") == "debug"


def test_get_path_unknown_section_raises():
    cfg = Config()
    with pytest.raises(KeyError):
        cfg.get_path("nonexistent.field")


def test_get_path_unknown_attr_raises():
    cfg = Config()
    with pytest.raises(KeyError):
        cfg.get_path("ui.nonexistent")


def test_get_path_invalid_format_raises():
    cfg = Config()
    with pytest.raises(KeyError):
        cfg.get_path("no_dots")
    with pytest.raises(KeyError):
        cfg.get_path("too.many.dots")
