from pathlib import Path

from mod_tui.persistence.paths import (
    global_config_dir,
    project_state_dir,
    project_layout_path,
    project_transcript_path,
    project_orchestrator_transcript,
)


def test_project_state_dir_is_cwd_dot_mod_tui(tmp_path: Path):
    assert project_state_dir(tmp_path) == tmp_path / ".mod_tui"


def test_project_layout_path(tmp_path: Path):
    assert project_layout_path(tmp_path) == tmp_path / ".mod_tui" / "layout.json"


def test_project_transcript_path(tmp_path: Path):
    assert project_transcript_path(tmp_path, "abc123") == (
        tmp_path / ".mod_tui" / "transcripts" / "abc123.jsonl"
    )


def test_project_orchestrator_transcript(tmp_path: Path):
    assert project_orchestrator_transcript(tmp_path) == (
        tmp_path / ".mod_tui" / "transcripts" / "orchestrator.jsonl"
    )


def test_global_config_dir_under_xdg_or_home(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert global_config_dir() == tmp_path / "mod_tui"

    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert global_config_dir() == tmp_path / ".config" / "mod_tui"
