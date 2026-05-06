from pathlib import Path

from mod_tui.persistence.transcript_store import (
    OrchestratorTranscript,
    TranscriptEntry,
)


def test_append_and_read_round_trip(tmp_path: Path):
    store = OrchestratorTranscript(cwd=tmp_path)
    store.append(TranscriptEntry(role="user", text="hello"))
    store.append(TranscriptEntry(role="orch", text="hi back"))

    entries = store.read_all()
    assert entries == [
        TranscriptEntry(role="user", text="hello"),
        TranscriptEntry(role="orch", text="hi back"),
    ]


def test_read_all_when_empty_returns_empty_list(tmp_path: Path):
    store = OrchestratorTranscript(cwd=tmp_path)
    assert store.read_all() == []


def test_append_creates_transcripts_dir(tmp_path: Path):
    store = OrchestratorTranscript(cwd=tmp_path)
    store.append(TranscriptEntry(role="user", text="x"))
    assert (tmp_path / ".mod_tui" / "transcripts" / "orchestrator.jsonl").exists()


def test_corrupted_line_is_skipped(tmp_path: Path):
    store = OrchestratorTranscript(cwd=tmp_path)
    store.append(TranscriptEntry(role="user", text="ok"))

    target = tmp_path / ".mod_tui" / "transcripts" / "orchestrator.jsonl"
    with target.open("a") as f:
        f.write("not json\n")
    store.append(TranscriptEntry(role="orch", text="still works"))

    entries = store.read_all()
    assert entries == [
        TranscriptEntry(role="user", text="ok"),
        TranscriptEntry(role="orch", text="still works"),
    ]
