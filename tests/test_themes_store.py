from pathlib import Path

import pytest

from patchbai.persistence.themes_store import NamedThemesStore
from patchbai.theme.spec import ThemePalette, ThemeSpec


def _spec() -> ThemeSpec:
    return ThemeSpec(palette=ThemePalette(primary="#005577"))


def test_save_and_load_round_trip(tmp_path: Path):
    store = NamedThemesStore(global_dir=tmp_path)
    store.save("nord-ish", _spec())
    assert store.load("nord-ish") == _spec()


def test_load_missing_returns_none(tmp_path: Path):
    store = NamedThemesStore(global_dir=tmp_path)
    assert store.load("nope") is None


def test_save_creates_themes_dir(tmp_path: Path):
    store = NamedThemesStore(global_dir=tmp_path)
    store.save("triage", _spec())
    assert (tmp_path / "themes" / "triage.json").exists()


def test_list_returns_saved_names_sorted(tmp_path: Path):
    store = NamedThemesStore(global_dir=tmp_path)
    store.save("c", _spec())
    store.save("a", _spec())
    store.save("b", _spec())
    assert store.list() == ["a", "b", "c"]


def test_load_invalid_file_returns_none(tmp_path: Path):
    themes = tmp_path / "themes"
    themes.mkdir()
    (themes / "broken.json").write_text("not json {{")
    store = NamedThemesStore(global_dir=tmp_path)
    assert store.load("broken") is None


def test_save_rejects_invalid_name(tmp_path: Path):
    store = NamedThemesStore(global_dir=tmp_path)
    with pytest.raises(ValueError):
        store.save("../escape", _spec())
    with pytest.raises(ValueError):
        store.save("name/with/slashes", _spec())
    with pytest.raises(ValueError):
        store.save("", _spec())
