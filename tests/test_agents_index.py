from pathlib import Path

from patchbai.agents.state import AgentInfo, AgentState
from patchbai.persistence.agents_index import AgentsIndex


def _info(id: str = "a1", state: AgentState = AgentState.IDLE) -> AgentInfo:
    return AgentInfo(id=id, name=f"agent-{id}", cwd="/tmp", started_at=100.0, state=state)


def test_load_returns_empty_when_no_file(tmp_path: Path):
    idx = AgentsIndex(cwd=tmp_path)
    assert idx.load() == []


def test_save_then_load_round_trips(tmp_path: Path):
    idx = AgentsIndex(cwd=tmp_path)
    idx.save([_info("a"), _info("b")])
    loaded = idx.load()
    assert [info.id for info in loaded] == ["a", "b"]


def test_save_creates_state_dir(tmp_path: Path):
    idx = AgentsIndex(cwd=tmp_path)
    idx.save([_info()])
    assert (tmp_path / ".patchbai" / "agents.json").exists()


def test_upsert_replaces_existing_by_id(tmp_path: Path):
    idx = AgentsIndex(cwd=tmp_path)
    idx.upsert(_info("a", state=AgentState.RUNNING))
    idx.upsert(_info("a", state=AgentState.DONE))
    loaded = idx.load()
    assert len(loaded) == 1
    assert loaded[0].state == AgentState.DONE


def test_upsert_appends_when_new(tmp_path: Path):
    idx = AgentsIndex(cwd=tmp_path)
    idx.upsert(_info("a"))
    idx.upsert(_info("b"))
    assert {info.id for info in idx.load()} == {"a", "b"}


def test_load_corrupted_file_returns_empty(tmp_path: Path):
    state = tmp_path / ".patchbai"
    state.mkdir()
    (state / "agents.json").write_text("not json {{")
    assert AgentsIndex(cwd=tmp_path).load() == []


def test_reconcile_orphans_flips_non_terminal_to_error(tmp_path: Path):
    idx = AgentsIndex(cwd=tmp_path)
    idx.save([
        _info("running", state=AgentState.RUNNING),
        _info("waiting", state=AgentState.WAITING),
        _info("idle", state=AgentState.IDLE),
        _info("done", state=AgentState.DONE),
        _info("error", state=AgentState.ERROR),
    ])

    reconciled = idx.reconcile_orphans()

    by_id = {info.id: info for info in reconciled}
    assert by_id["running"].state == AgentState.ERROR
    assert by_id["waiting"].state == AgentState.ERROR
    assert by_id["idle"].state == AgentState.ERROR
    # Already-terminal records must not be touched.
    assert by_id["done"].state == AgentState.DONE
    assert by_id["error"].state == AgentState.ERROR
    # Orphans get an ended_at stamped if they didn't have one.
    assert by_id["running"].ended_at is not None
    # And the persisted file matches the in-memory result.
    persisted = {info.id: info.state for info in idx.load()}
    assert persisted["running"] == AgentState.ERROR
    assert persisted["done"] == AgentState.DONE


def test_reconcile_orphans_skips_orchestrator(tmp_path: Path):
    # The orchestrator owns its own boot lifecycle and overwrites the entry
    # on start(), so reconcile must leave its row alone rather than fight it.
    idx = AgentsIndex(cwd=tmp_path)
    idx.save([
        AgentInfo(id="orchestrator", name="orchestrator", cwd="/tmp",
                  started_at=100.0, state=AgentState.RUNNING),
    ])

    reconciled = idx.reconcile_orphans()

    assert reconciled[0].id == "orchestrator"
    assert reconciled[0].state == AgentState.RUNNING


def test_reconcile_orphans_no_changes_when_all_terminal(tmp_path: Path):
    idx = AgentsIndex(cwd=tmp_path)
    idx.save([_info("a", state=AgentState.DONE)])
    mtime_before = (tmp_path / ".patchbai" / "agents.json").stat().st_mtime_ns
    idx.reconcile_orphans()
    # No write should happen; mtime unchanged.
    mtime_after = (tmp_path / ".patchbai" / "agents.json").stat().st_mtime_ns
    assert mtime_after == mtime_before
