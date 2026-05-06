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


def test_transcript_entry_round_trips_tool_fields(tmp_path):
    from mod_tui.persistence.transcript_store import AgentTranscript, TranscriptEntry

    t = AgentTranscript(cwd=tmp_path, agent_id="x")
    t.append(TranscriptEntry(role="tool_use", text="ls /tmp",
                             tool_id="toolu_1", tool_name="bash"))
    t.append(TranscriptEntry(role="tool_result", text="<output>",
                             tool_id="toolu_1"))

    entries = t.read_all()
    assert entries[0].tool_id == "toolu_1"
    assert entries[0].tool_name == "bash"
    assert entries[1].tool_id == "toolu_1"
    assert entries[1].tool_name is None


def test_transcript_entry_reads_old_records_without_tool_fields(tmp_path):
    """Records written before tool_id/tool_name existed must still load."""
    import json
    from mod_tui.persistence.paths import (
        project_transcript_path, project_transcripts_dir,
    )
    from mod_tui.persistence.transcript_store import AgentTranscript

    project_transcripts_dir(tmp_path).mkdir(parents=True, exist_ok=True)
    path = project_transcript_path(tmp_path, "old")
    path.write_text(json.dumps({"role": "assistant", "text": "hi"}) + "\n",
                    encoding="utf-8")

    entries = AgentTranscript(cwd=tmp_path, agent_id="old").read_all()
    assert len(entries) == 1
    assert entries[0].role == "assistant"
    assert entries[0].text == "hi"
    assert entries[0].tool_id is None
    assert entries[0].tool_name is None
