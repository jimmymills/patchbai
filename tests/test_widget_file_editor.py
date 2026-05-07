from pathlib import Path

import pytest
from textual.app import App

from mod_tui.widgets.file_editor import FileEditor


class _Host(App):
    def __init__(self, file_path: str | None = None):
        super().__init__()
        self._file_path = file_path

    def compose(self):
        if self._file_path is None:
            yield FileEditor()
        else:
            yield FileEditor(file_path=self._file_path)


@pytest.mark.asyncio
async def test_file_editor_loads_text_content(tmp_path: Path):
    p = tmp_path / "hello.py"
    p.write_text("print('hi')\n", encoding="utf-8")

    app = _Host(str(p))
    async with app.run_test() as pilot:
        await pilot.pause()
        editor = app.query_one(FileEditor)
        assert editor.text.startswith("print('hi')")


@pytest.mark.asyncio
async def test_file_editor_detects_python_language(tmp_path: Path):
    p = tmp_path / "x.py"
    p.write_text("x = 1\n", encoding="utf-8")

    app = _Host(str(p))
    async with app.run_test() as pilot:
        await pilot.pause()
        editor = app.query_one(FileEditor)
        assert editor.language == "python"


@pytest.mark.asyncio
async def test_file_editor_missing_file_shows_error(tmp_path: Path):
    app = _Host(str(tmp_path / "nope.txt"))
    async with app.run_test() as pilot:
        await pilot.pause()
        editor = app.query_one(FileEditor)
        assert "not found" in editor.text.lower()


@pytest.mark.asyncio
async def test_file_editor_is_writable_by_default():
    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        editor = app.query_one(FileEditor)
        assert editor.read_only is False


@pytest.mark.asyncio
async def test_file_editor_blank_when_no_path():
    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        editor = app.query_one(FileEditor)
        assert editor.text == ""
        assert editor.language is None
