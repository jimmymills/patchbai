from pathlib import Path

import pytest
from textual.app import App

from mod_tui.widgets.file_viewer import FileViewer


class _Host(App):
    def __init__(self, file_path: str):
        super().__init__()
        self._file_path = file_path

    def compose(self):
        yield FileViewer(file_path=self._file_path)


@pytest.mark.asyncio
async def test_file_viewer_loads_text_content(tmp_path: Path):
    p = tmp_path / "hello.py"
    p.write_text("print('hi')\n", encoding="utf-8")

    app = _Host(str(p))
    async with app.run_test() as pilot:
        await pilot.pause()
        viewer = app.query_one(FileViewer)
        assert viewer.text.startswith("print('hi')")


@pytest.mark.asyncio
async def test_file_viewer_detects_python_language(tmp_path: Path):
    p = tmp_path / "x.py"
    p.write_text("x = 1\n", encoding="utf-8")

    app = _Host(str(p))
    async with app.run_test() as pilot:
        await pilot.pause()
        viewer = app.query_one(FileViewer)
        assert viewer.language == "python"


@pytest.mark.asyncio
async def test_file_viewer_missing_file_shows_error(tmp_path: Path):
    app = _Host(str(tmp_path / "nope.txt"))
    async with app.run_test() as pilot:
        await pilot.pause()
        viewer = app.query_one(FileViewer)
        assert "not found" in viewer.text.lower()
