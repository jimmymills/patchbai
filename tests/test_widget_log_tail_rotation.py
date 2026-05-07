from pathlib import Path

import pytest
from textual.app import App

from patchbai.widgets.log_tail import LogTail


class _Host(App):
    def __init__(self, file_path: str):
        super().__init__()
        self._file_path = file_path

    def compose(self):
        yield LogTail(file_path=self._file_path)


@pytest.mark.asyncio
async def test_log_tail_reopens_after_rotation(tmp_path: Path):
    p = tmp_path / "x.log"
    p.write_text("first\n")

    app = _Host(str(p))
    async with app.run_test() as pilot:
        await pilot.pause()
        tail = app.query_one(LogTail)
        assert "first" in tail.text

        # Rotate: rename the existing file and create a fresh one with the
        # same name. The widget should detect the inode change on the next
        # tick and reopen.
        rotated = tmp_path / "x.log.1"
        p.rename(rotated)
        p.write_text("after rotation\n")

        for _ in range(5):
            tail._tick()
            await pilot.pause()

        assert "after rotation" in tail.text
