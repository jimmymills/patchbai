from pathlib import Path

import pytest
from textual.app import App

from mod_tui.widgets.notebook import Notebook


class _Host(App):
    def __init__(self, name: str, cwd: Path):
        super().__init__()
        self.cwd = cwd
        self._name = name

    def compose(self):
        yield Notebook(name=self._name)


@pytest.mark.asyncio
async def test_notebook_loads_existing_content(tmp_path: Path):
    scratch = tmp_path / ".mod_tui" / "scratch"
    scratch.mkdir(parents=True)
    (scratch / "todo.md").write_text("- one\n- two\n", encoding="utf-8")

    app = _Host("todo", tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        nb = app.query_one(Notebook)
        assert "one" in nb.text
        assert "two" in nb.text


@pytest.mark.asyncio
async def test_notebook_persists_edits(tmp_path: Path):
    app = _Host("todo", tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        nb = app.query_one(Notebook)
        nb.text = "- new entry\n"
        # Manually trigger save (Notebook saves on text-changed; we drive it).
        nb._save()
        await pilot.pause()

    saved = (tmp_path / ".mod_tui" / "scratch" / "todo.md").read_text(encoding="utf-8")
    assert "new entry" in saved


@pytest.mark.asyncio
async def test_notebook_creates_scratch_dir(tmp_path: Path):
    app = _Host("fresh", tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        nb = app.query_one(Notebook)
        nb.text = "x\n"
        nb._save()
        await pilot.pause()
    assert (tmp_path / ".mod_tui" / "scratch" / "fresh.md").exists()
