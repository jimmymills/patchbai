from pathlib import Path

import pytest

from mod_tui.layout.spec import LayoutSpec
from mod_tui.persistence.layouts_store import NamedLayoutsStore


def _spec() -> LayoutSpec:
    return LayoutSpec.model_validate({
        "version": 1,
        "layout": {"id": "orch", "widget": "OrchestratorChat"},
    })


def test_save_and_load_round_trip(tmp_path: Path):
    store = NamedLayoutsStore(global_dir=tmp_path)
    store.save("triage", _spec())
    assert store.load("triage") == _spec()


def test_load_missing_returns_none(tmp_path: Path):
    store = NamedLayoutsStore(global_dir=tmp_path)
    assert store.load("nope") is None


def test_save_creates_layouts_dir(tmp_path: Path):
    store = NamedLayoutsStore(global_dir=tmp_path)
    store.save("triage", _spec())
    assert (tmp_path / "layouts" / "triage.json").exists()


def test_list_returns_saved_names_sorted(tmp_path: Path):
    store = NamedLayoutsStore(global_dir=tmp_path)
    store.save("triage", _spec())
    store.save("deep-dive", _spec())
    store.save("review", _spec())
    assert store.list() == ["deep-dive", "review", "triage"]


def test_load_invalid_file_returns_none(tmp_path: Path):
    layouts = tmp_path / "layouts"
    layouts.mkdir()
    (layouts / "broken.json").write_text("not json {{")
    store = NamedLayoutsStore(global_dir=tmp_path)
    assert store.load("broken") is None


def test_save_rejects_invalid_name(tmp_path: Path):
    store = NamedLayoutsStore(global_dir=tmp_path)
    with pytest.raises(ValueError):
        store.save("../escape", _spec())
    with pytest.raises(ValueError):
        store.save("name/with/slashes", _spec())
    with pytest.raises(ValueError):
        store.save("", _spec())
