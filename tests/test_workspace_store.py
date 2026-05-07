import json

from mod_tui.persistence.workspace_store import (
    load_workspace,
    save_workspace,
)
from mod_tui.workspace.spec import Workspace


def _ws() -> Workspace:
    return Workspace.model_validate({
        "version": 1,
        "tabs": [
            {
                "id": "t1",
                "title": "Main",
                "layout": {
                    "version": 1,
                    "layout": {"id": "orch", "widget": "OrchestratorChat"},
                },
            },
        ],
        "active": "t1",
    })


def test_save_then_load_round_trips(tmp_path):
    src = _ws()
    save_workspace(tmp_path, src)
    again = load_workspace(tmp_path)
    assert again == src


def test_load_returns_none_when_no_file(tmp_path):
    assert load_workspace(tmp_path) is None


def test_load_returns_none_for_corrupt_file(tmp_path):
    target = tmp_path / ".mod_tui" / "workspace.json"
    target.parent.mkdir(parents=True)
    target.write_text("{not json")
    assert load_workspace(tmp_path) is None


def test_save_writes_to_workspace_json(tmp_path):
    save_workspace(tmp_path, _ws())
    target = tmp_path / ".mod_tui" / "workspace.json"
    assert target.exists()
    raw = json.loads(target.read_text())
    assert raw["active"] == "t1"
    assert [t["id"] for t in raw["tabs"]] == ["t1"]
