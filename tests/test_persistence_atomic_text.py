from pathlib import Path

from patchfeld.persistence.atomic import write_text_atomic


def test_write_text_atomic_round_trip(tmp_path):
    p = tmp_path / "out.txt"
    write_text_atomic(p, "hello world")
    assert p.read_text(encoding="utf-8") == "hello world"


def test_write_text_atomic_creates_parent(tmp_path):
    p = tmp_path / "nested" / "deep" / "out.txt"
    write_text_atomic(p, "nested")
    assert p.read_text(encoding="utf-8") == "nested"


def test_write_text_atomic_leaves_no_tmp_files(tmp_path):
    p = tmp_path / "out.txt"
    write_text_atomic(p, "x")
    leftovers = [f.name for f in tmp_path.iterdir() if f.name != "out.txt"]
    assert leftovers == []


def test_write_text_atomic_overwrites_existing(tmp_path):
    p = tmp_path / "out.txt"
    write_text_atomic(p, "v1")
    write_text_atomic(p, "v2")
    assert p.read_text(encoding="utf-8") == "v2"
