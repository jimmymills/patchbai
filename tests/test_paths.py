from pathlib import Path

from patchbai.persistence.paths import (
    global_config_dir,
    project_state_dir,
    project_layout_path,
    project_transcript_path,
    project_orchestrator_transcript,
)


def test_project_state_dir_is_cwd_dot_patchbai(tmp_path: Path):
    assert project_state_dir(tmp_path) == tmp_path / ".patchbai"


def test_project_layout_path(tmp_path: Path):
    assert project_layout_path(tmp_path) == tmp_path / ".patchbai" / "layout.json"


def test_project_transcript_path(tmp_path: Path):
    assert project_transcript_path(tmp_path, "abc123") == (
        tmp_path / ".patchbai" / "transcripts" / "abc123.jsonl"
    )


def test_project_orchestrator_transcript(tmp_path: Path):
    assert project_orchestrator_transcript(tmp_path) == (
        tmp_path / ".patchbai" / "transcripts" / "orchestrator.jsonl"
    )


def test_global_config_dir_under_xdg_or_home(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert global_config_dir() == tmp_path / "patchbai"

    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert global_config_dir() == tmp_path / ".config" / "patchbai"


def test_orchestrator_session_transcript_path_uses_session_id(tmp_path):
    from patchbai.persistence.paths import orchestrator_session_transcript_path
    p = orchestrator_session_transcript_path(tmp_path, "abc-123")
    assert p == tmp_path / ".patchbai" / "transcripts" / "orchestrator.abc-123.jsonl"
