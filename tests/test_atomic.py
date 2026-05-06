import json
from pathlib import Path

import pytest

from mod_tui.persistence.atomic import write_json_atomic


def test_writes_file(tmp_path: Path):
    target = tmp_path / "out.json"
    write_json_atomic(target, {"a": 1, "b": [2, 3]})
    assert json.loads(target.read_text()) == {"a": 1, "b": [2, 3]}


def test_creates_parent_dirs(tmp_path: Path):
    target = tmp_path / "deep" / "nested" / "x.json"
    write_json_atomic(target, {"ok": True})
    assert target.exists()


def test_overwrites_existing_file(tmp_path: Path):
    target = tmp_path / "x.json"
    target.write_text('{"old": true}')
    write_json_atomic(target, {"new": True})
    assert json.loads(target.read_text()) == {"new": True}


def test_no_temp_file_left_after_success(tmp_path: Path):
    target = tmp_path / "x.json"
    write_json_atomic(target, {"a": 1})
    siblings = [p.name for p in tmp_path.iterdir()]
    assert siblings == ["x.json"]


def test_no_temp_file_left_after_failure(tmp_path: Path):
    target = tmp_path / "x.json"
    with pytest.raises(TypeError):
        write_json_atomic(target, object())  # not JSON-serializable
    assert not any(tmp_path.iterdir())
