from pathlib import Path

from mod_tui.persistence.transcript_store import (
    AgentTranscript,
    OrchestratorTranscript,
    TranscriptEntry,
)


def test_per_agent_transcript_writes_to_agent_id_path(tmp_path: Path):
    t = AgentTranscript(cwd=tmp_path, agent_id="abc123")
    t.append(TranscriptEntry(role="user", text="hi"))
    expected = tmp_path / ".mod_tui" / "transcripts" / "abc123.jsonl"
    assert expected.exists()


def test_per_agent_transcript_round_trip(tmp_path: Path):
    t = AgentTranscript(cwd=tmp_path, agent_id="agent-1")
    t.append(TranscriptEntry(role="assistant", text="ok"))
    t.append(TranscriptEntry(role="tool_use", text="bash: ls"))
    assert t.read_all() == [
        TranscriptEntry(role="assistant", text="ok"),
        TranscriptEntry(role="tool_use", text="bash: ls"),
    ]


def test_two_agents_write_to_different_files(tmp_path: Path):
    a = AgentTranscript(cwd=tmp_path, agent_id="a")
    b = AgentTranscript(cwd=tmp_path, agent_id="b")
    a.append(TranscriptEntry(role="user", text="ping a"))
    b.append(TranscriptEntry(role="user", text="ping b"))

    assert a.read_all() == [TranscriptEntry(role="user", text="ping a")]
    assert b.read_all() == [TranscriptEntry(role="user", text="ping b")]


def test_orchestrator_transcript_still_works_unchanged(tmp_path: Path):
    # Backwards compatibility — OrchestratorTranscript is the alias we used in plan 1.
    o = OrchestratorTranscript(cwd=tmp_path)
    o.append(TranscriptEntry(role="user", text="legacy"))
    assert o.read_all() == [TranscriptEntry(role="user", text="legacy")]
    # And the file path is the canonical orchestrator file.
    assert (tmp_path / ".mod_tui" / "transcripts" / "orchestrator.jsonl").exists()
