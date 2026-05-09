from pathlib import Path

import pytest

from patchfeld.config import ConfigStore, KeyBinding


def test_load_returns_defaults_when_no_file(tmp_path: Path):
    store = ConfigStore(global_dir=tmp_path)
    cfg = store.load()
    assert cfg.bindings  # non-empty defaults
    assert cfg.ui.active_theme == "default"
    assert cfg.ui.default_model == ""


def test_save_then_load_round_trip(tmp_path: Path):
    store = ConfigStore(global_dir=tmp_path)
    cfg = store.load()
    cfg.bindings["~"] = KeyBinding(action="focus_orchestrator", args={})
    cfg.ui.active_theme = "nord"
    store.save(cfg)

    again = store.load()
    assert again.bindings["~"].action == "focus_orchestrator"
    assert again.ui.active_theme == "nord"


def test_save_creates_config_dir(tmp_path: Path):
    store = ConfigStore(global_dir=tmp_path)
    store.save(store.load())
    assert (tmp_path / "config.toml").exists()


def test_set_path_dotted(tmp_path: Path):
    store = ConfigStore(global_dir=tmp_path)
    cfg = store.load()
    cfg.set_path("ui.active_theme", "nord")
    assert cfg.ui.active_theme == "nord"

    cfg.set_path("ui.default_model", "claude-sonnet-4-6")
    assert cfg.ui.default_model == "claude-sonnet-4-6"


def test_get_path_dotted(tmp_path: Path):
    store = ConfigStore(global_dir=tmp_path)
    cfg = store.load()
    assert cfg.get_path("ui.active_theme") == "default"
    assert cfg.get_path("ui.default_model") == ""


def test_get_path_unknown_raises(tmp_path: Path):
    store = ConfigStore(global_dir=tmp_path)
    cfg = store.load()
    with pytest.raises(KeyError):
        cfg.get_path("nonexistent.field")


def test_active_theme_defaults_to_default(tmp_path: Path):
    store = ConfigStore(global_dir=tmp_path)
    cfg = store.load()
    assert cfg.ui.active_theme == "default"


def test_active_theme_persists_round_trip(tmp_path: Path):
    store = ConfigStore(global_dir=tmp_path)
    cfg = store.load()
    cfg.ui.active_theme = "nord"
    store.save(cfg)

    reloaded = ConfigStore(global_dir=tmp_path).load()
    assert reloaded.ui.active_theme == "nord"


def test_load_does_not_re_parse_when_file_unchanged(tmp_path: Path):
    """The orchestrator hot path calls config_store.load() inside many
    tool handlers; back-to-back calls with no intervening write must
    not re-parse the TOML each time."""
    store = ConfigStore(global_dir=tmp_path)
    cfg = store.load()
    cfg.ui.active_theme = "nord"
    store.save(cfg)

    parse_calls = 0
    real_load = store._load_from_disk

    def counting_load() -> object:
        nonlocal parse_calls
        parse_calls += 1
        return real_load()

    store._load_from_disk = counting_load  # type: ignore[method-assign]

    for _ in range(10):
        store.load()

    assert parse_calls == 0, f"expected no re-parses, got {parse_calls}"


def test_load_picks_up_external_edit_via_mtime(tmp_path: Path):
    """If config.toml changes on disk between loads (a sibling
    ConfigStore wrote, or the user edited by hand), the next load
    must observe the new state — the cache is mtime-keyed, not
    forever."""
    import os
    import time as _time

    store = ConfigStore(global_dir=tmp_path)
    cfg = store.load()
    cfg.ui.active_theme = "nord"
    store.save(cfg)

    # Hand-edit the file out from under the store, then bump mtime so
    # the cache definitely sees a change even on coarse-mtime filesystems.
    p = tmp_path / "config.toml"
    p.write_text(
        '[ui]\nactive_theme = "rose"\ndefault_model = ""\n[bindings]\n[widgets]\n',
        encoding="utf-8",
    )
    later = _time.time() + 1
    os.utime(p, (later, later))

    again = store.load()
    assert again.ui.active_theme == "rose"


def test_legacy_ui_theme_key_silently_ignored(tmp_path: Path):
    """Old configs with `ui.theme = "dark"` must still load without raising;
    the dead key is just ignored."""
    (tmp_path / "config.toml").write_text(
        '[ui]\ntheme = "dark"\ndefault_model = ""\n',
        encoding="utf-8",
    )
    store = ConfigStore(global_dir=tmp_path)
    cfg = store.load()
    assert cfg.ui.active_theme == "default"
