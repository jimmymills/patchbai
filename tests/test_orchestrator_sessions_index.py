from pathlib import Path

from mod_tui.persistence.orchestrator_sessions import (
    OrchestratorSessionEntry,
    OrchestratorSessionsIndex,
)


def _entry(sid: str = "s1", last: float = 100.0, legacy: bool = False) -> OrchestratorSessionEntry:
    return OrchestratorSessionEntry(
        session_id=sid,
        transcript_path=f".mod_tui/transcripts/orchestrator.{sid}.jsonl",
        started_at=last - 10,
        last_activity=last,
        first_user_message=None,
        num_turns=0,
        tokens_in=0,
        tokens_out=0,
        cost=0.0,
        legacy=legacy,
    )


def test_list_returns_empty_when_no_file(tmp_path):
    idx = OrchestratorSessionsIndex(cwd=tmp_path)
    assert idx.list() == []


def test_upsert_then_list_round_trips(tmp_path):
    idx = OrchestratorSessionsIndex(cwd=tmp_path)
    idx.upsert(_entry("a"))
    idx.upsert(_entry("b"))
    out = idx.list()
    assert {e.session_id for e in out} == {"a", "b"}


def test_upsert_replaces_existing_by_session_id(tmp_path):
    idx = OrchestratorSessionsIndex(cwd=tmp_path)
    idx.upsert(_entry("a", last=100.0))
    idx.upsert(_entry("a", last=200.0))
    out = idx.list()
    assert len(out) == 1
    assert out[0].last_activity == 200.0


def test_most_recent_returns_max_last_activity(tmp_path):
    idx = OrchestratorSessionsIndex(cwd=tmp_path)
    idx.upsert(_entry("old", last=100.0))
    idx.upsert(_entry("new", last=300.0))
    idx.upsert(_entry("mid", last=200.0))
    assert idx.most_recent().session_id == "new"


def test_most_recent_is_none_when_empty(tmp_path):
    idx = OrchestratorSessionsIndex(cwd=tmp_path)
    assert idx.most_recent() is None


def test_get_returns_entry_by_session_id(tmp_path):
    idx = OrchestratorSessionsIndex(cwd=tmp_path)
    idx.upsert(_entry("abc"))
    assert idx.get("abc").session_id == "abc"
    assert idx.get("missing") is None


def test_corrupt_file_is_treated_as_empty(tmp_path):
    state = tmp_path / ".mod_tui"
    state.mkdir()
    (state / "orchestrator_sessions.json").write_text("not json {{")
    assert OrchestratorSessionsIndex(cwd=tmp_path).list() == []


def test_index_persists_to_expected_path(tmp_path):
    idx = OrchestratorSessionsIndex(cwd=tmp_path)
    idx.upsert(_entry("a"))
    assert (tmp_path / ".mod_tui" / "orchestrator_sessions.json").exists()
