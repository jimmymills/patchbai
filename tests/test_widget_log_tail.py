from pathlib import Path

import pytest
from textual.app import App

from patchfeld.widgets.log_tail import LogTail


class _Host(App):
    def __init__(self, file_path: str):
        super().__init__()
        self._file_path = file_path

    def compose(self):
        yield LogTail(file_path=self._file_path)


@pytest.mark.asyncio
async def test_log_tail_renders_existing_content(tmp_path: Path):
    p = tmp_path / "x.log"
    p.write_text("first line\nsecond line\n")

    app = _Host(str(p))
    async with app.run_test() as pilot:
        await pilot.pause()
        tail = app.query_one(LogTail)
        assert "first line" in tail.text
        assert "second line" in tail.text


@pytest.mark.asyncio
async def test_log_tail_appends_new_lines(tmp_path: Path):
    p = tmp_path / "x.log"
    p.write_text("initial\n")

    app = _Host(str(p))
    async with app.run_test() as pilot:
        await pilot.pause()
        tail = app.query_one(LogTail)
        assert "initial" in tail.text

        # Append while the widget is mounted.
        with p.open("a") as f:
            f.write("appended\n")

        # Trigger the tick manually (avoids waiting for the 250ms timer).
        tail._tick()
        await pilot.pause()
        assert "appended" in tail.text


@pytest.mark.asyncio
async def test_log_tail_missing_file_shows_error(tmp_path: Path):
    app = _Host(str(tmp_path / "nope.log"))
    async with app.run_test() as pilot:
        await pilot.pause()
        tail = app.query_one(LogTail)
        assert "not found" in tail.text.lower()
