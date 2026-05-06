from pathlib import Path

import pytest
from textual.app import App

from mod_tui.widgets.file_tree import FileTree


class _Host(App):
    def __init__(self, path: str):
        super().__init__()
        self._path = path

    def compose(self):
        yield FileTree(path=self._path)


@pytest.mark.asyncio
async def test_file_tree_mounts_with_path(tmp_path: Path):
    (tmp_path / "a.txt").write_text("a")
    (tmp_path / "subdir").mkdir()

    app = _Host(str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        tree = app.query_one(FileTree)
        assert str(tree.path) == str(tmp_path)
