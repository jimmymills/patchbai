from pathlib import Path

from patchbai.persistence.orchestrator_sessions import (
    OrchestratorSessionEntry,
    OrchestratorSessionsIndex,
)


def _entry(sid: str = "s1", last: float = 100.0, legacy: bool = False) -> OrchestratorSessionEntry:
    return OrchestratorSessionEntry(
        session_id=sid,
        transcript_path=f".patchbai/transcripts/orchestrator.{sid}.jsonl",
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
    state = tmp_path / ".patchbai"
    state.mkdir()
    (state / "orchestrator_sessions.json").write_text("not json {{")
    assert OrchestratorSessionsIndex(cwd=tmp_path).list() == []


def test_index_persists_to_expected_path(tmp_path):
    idx = OrchestratorSessionsIndex(cwd=tmp_path)
    idx.upsert(_entry("a"))
    assert (tmp_path / ".patchbai" / "orchestrator_sessions.json").exists()


def test_migrate_legacy_when_old_jsonl_exists_no_index(tmp_path):
    transcripts = tmp_path / ".patchbai" / "transcripts"
    transcripts.mkdir(parents=True)
    legacy = transcripts / "orchestrator.jsonl"
    legacy.write_text('{"role": "user", "text": "old"}\n', encoding="utf-8")
    idx = OrchestratorSessionsIndex(cwd=tmp_path)

    idx.migrate_legacy_if_needed()

    assert not legacy.exists()
    renamed = list(transcripts.glob("orchestrator.legacy-*.jsonl"))
    assert len(renamed) == 1
    entries = idx.list()
    assert len(entries) == 1
    assert entries[0].legacy is True
    assert entries[0].session_id.startswith("legacy-")
    assert entries[0].transcript_path.endswith(renamed[0].name)


def test_migrate_legacy_is_idempotent(tmp_path):
    transcripts = tmp_path / ".patchbai" / "transcripts"
    transcripts.mkdir(parents=True)
    (transcripts / "orchestrator.jsonl").write_text("{}\n", encoding="utf-8")
    idx = OrchestratorSessionsIndex(cwd=tmp_path)
    idx.migrate_legacy_if_needed()
    before = idx.list()
    idx.migrate_legacy_if_needed()
    after = idx.list()
    assert before == after


def test_migrate_legacy_noop_when_no_legacy_file(tmp_path):
    idx = OrchestratorSessionsIndex(cwd=tmp_path)
    idx.migrate_legacy_if_needed()
    assert idx.list() == []


def test_migrate_legacy_noop_when_index_already_exists(tmp_path):
    transcripts = tmp_path / ".patchbai" / "transcripts"
    transcripts.mkdir(parents=True)
    (transcripts / "orchestrator.jsonl").write_text("{}\n", encoding="utf-8")
    idx = OrchestratorSessionsIndex(cwd=tmp_path)
    idx.upsert(_entry("real"))  # creates orchestrator_sessions.json

    idx.migrate_legacy_if_needed()

    # Legacy file still exists — migration only runs on a clean index.
    assert (transcripts / "orchestrator.jsonl").exists()
    assert {e.session_id for e in idx.list()} == {"real"}
