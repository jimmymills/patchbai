from pathlib import Path

import pytest

from mod_tui.config import ConfigStore, KeyBinding


def test_load_returns_defaults_when_no_file(tmp_path: Path):
    store = ConfigStore(global_dir=tmp_path)
    cfg = store.load()
    assert cfg.bindings  # non-empty defaults
    assert cfg.ui.theme == "dark"
    assert cfg.ui.default_model == ""


def test_save_then_load_round_trip(tmp_path: Path):
    store = ConfigStore(global_dir=tmp_path)
    cfg = store.load()
    cfg.bindings["~"] = KeyBinding(action="focus_orchestrator", args={})
    cfg.ui.theme = "light"
    store.save(cfg)

    again = store.load()
    assert again.bindings["~"].action == "focus_orchestrator"
    assert again.ui.theme == "light"


def test_save_creates_config_dir(tmp_path: Path):
    store = ConfigStore(global_dir=tmp_path)
    store.save(store.load())
    assert (tmp_path / "config.toml").exists()


def test_set_path_dotted(tmp_path: Path):
    store = ConfigStore(global_dir=tmp_path)
    cfg = store.load()
    cfg.set_path("ui.theme", "light")
    assert cfg.ui.theme == "light"

    cfg.set_path("ui.default_model", "claude-sonnet-4-6")
    assert cfg.ui.default_model == "claude-sonnet-4-6"


def test_get_path_dotted(tmp_path: Path):
    store = ConfigStore(global_dir=tmp_path)
    cfg = store.load()
    assert cfg.get_path("ui.theme") == "dark"
    assert cfg.get_path("ui.default_model") == ""


def test_get_path_unknown_raises(tmp_path: Path):
    store = ConfigStore(global_dir=tmp_path)
    cfg = store.load()
    with pytest.raises(KeyError):
        cfg.get_path("nonexistent.field")
