from pathlib import Path

from mod_tui.persistence.transcript_store import (
    AgentTranscript,
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


def test_agent_transcript_path_override_uses_explicit_path(tmp_path):
    custom = tmp_path / "custom_dir" / "my_session.jsonl"
    t = AgentTranscript(cwd=tmp_path, agent_id="orchestrator", path=custom)
    t.append(TranscriptEntry(role="user", text="hi"))
    assert custom.exists()
    assert (tmp_path / ".mod_tui" / "transcripts" / "orchestrator.jsonl").exists() is False


def test_agent_transcript_path_override_creates_parents(tmp_path):
    custom = tmp_path / "deep" / "nested" / "x.jsonl"
    t = AgentTranscript(cwd=tmp_path, agent_id="ignored", path=custom)
    t.append(TranscriptEntry(role="user", text="hi"))
    assert custom.exists()


def test_agent_transcript_path_override_reads_back(tmp_path):
    custom = tmp_path / "x.jsonl"
    t = AgentTranscript(cwd=tmp_path, agent_id="ignored", path=custom)
    t.append(TranscriptEntry(role="user", text="hello"))
    t.append(TranscriptEntry(role="assistant", text="hi"))
    out = AgentTranscript(cwd=tmp_path, agent_id="ignored", path=custom).read_all()
    assert [e.text for e in out] == ["hello", "hi"]
