from pathlib import Path

from mod_tui.layout.spec import LayoutSpec
from mod_tui.persistence.layout_store import load_layout, save_layout


def _spec() -> LayoutSpec:
    return LayoutSpec.model_validate({
        "version": 1,
        "layout": {"id": "orch", "widget": "OrchestratorChat"},
    })


def test_load_returns_none_when_no_file(tmp_path: Path):
    assert load_layout(tmp_path) is None


def test_save_then_load_round_trips(tmp_path: Path):
    save_layout(tmp_path, _spec())
    loaded = load_layout(tmp_path)
    assert loaded == _spec()


def test_save_creates_state_dir(tmp_path: Path):
    save_layout(tmp_path, _spec())
    assert (tmp_path / ".mod_tui" / "layout.json").exists()


def test_load_corrupted_file_returns_none(tmp_path: Path):
    state = tmp_path / ".mod_tui"
    state.mkdir()
    (state / "layout.json").write_text("not json {{")
    assert load_layout(tmp_path) is None


def test_load_invalid_spec_returns_none(tmp_path: Path):
    state = tmp_path / ".mod_tui"
    state.mkdir()
    (state / "layout.json").write_text(
        '{"version": 1, "layout": {"id": "x", "widget": "AgentTable"}}'
    )
    # Missing OrchestratorChat — invariant violated.
    assert load_layout(tmp_path) is None
