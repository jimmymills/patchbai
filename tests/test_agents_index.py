from pathlib import Path

from mod_tui.agents.state import AgentInfo, AgentState
from mod_tui.persistence.agents_index import AgentsIndex


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
    assert (tmp_path / ".mod_tui" / "agents.json").exists()


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
    state = tmp_path / ".mod_tui"
    state.mkdir()
    (state / "agents.json").write_text("not json {{")
    assert AgentsIndex(cwd=tmp_path).load() == []
