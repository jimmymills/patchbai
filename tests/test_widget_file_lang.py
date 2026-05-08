from pathlib import Path

from patchfeld.widgets._file_lang import detect_language, load_text


def test_detect_language_python():
    assert detect_language(Path("foo.py")) == "python"


def test_detect_language_unknown_returns_none():
    assert detect_language(Path("foo.xyz")) is None


def test_detect_language_is_case_insensitive():
    assert detect_language(Path("foo.PY")) == "python"


def test_load_text_reads_existing_file(tmp_path: Path):
    p = tmp_path / "x.py"
    p.write_text("print('hi')\n", encoding="utf-8")

    text, language = load_text(p)

    assert text == "print('hi')\n"
    assert language == "python"


def test_load_text_missing_file_returns_placeholder(tmp_path: Path):
    p = tmp_path / "nope.txt"

    text, language = load_text(p)

    assert "not found" in text.lower()
    assert language is None
