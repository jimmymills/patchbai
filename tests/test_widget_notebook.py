from pathlib import Path

import pytest
from textual.app import App

from patchfeld.widgets.notebook import Notebook


class _Host(App):
    def __init__(self, name: str, cwd: Path):
        super().__init__()
        self.cwd = cwd
        self._name = name

    def compose(self):
        yield Notebook(name=self._name)


@pytest.mark.asyncio
async def test_notebook_loads_existing_content(tmp_path: Path):
    scratch = tmp_path / ".patchfeld" / "scratch"
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

    saved = (tmp_path / ".patchfeld" / "scratch" / "todo.md").read_text(encoding="utf-8")
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
    assert (tmp_path / ".patchfeld" / "scratch" / "fresh.md").exists()


@pytest.mark.asyncio
async def test_typing_does_not_save_synchronously(tmp_path: Path):
    """A single keystroke must NOT trigger a disk write — saves are
    debounced. The file should still match the on-disk value (empty
    or whatever was loaded) immediately after the change event."""
    scratch = tmp_path / ".patchfeld" / "scratch" / "todo.md"

    app = _Host("todo", tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        nb = app.query_one(Notebook)
        # Drive the change event but don't wait for the debounce.
        nb.text = "burst1"
        await pilot.pause(0)

        assert not scratch.exists(), (
            "synchronous save during typing would defeat the debounce"
        )


@pytest.mark.asyncio
async def test_rapid_changes_coalesce_into_one_save(tmp_path: Path):
    """Multiple changes within the debounce window must collapse to a
    single write, and only the FINAL text reaches disk."""
    scratch = tmp_path / ".patchfeld" / "scratch" / "todo.md"

    app = _Host("todo", tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        nb = app.query_one(Notebook)
        nb.text = "first"
        await pilot.pause(0)
        nb.text = "second"
        await pilot.pause(0)
        nb.text = "third"
        # Force the debounce to fire by flushing.
        nb.flush_pending_save()

        await pilot.pause()
    assert scratch.read_text(encoding="utf-8") == "third"


@pytest.mark.asyncio
async def test_unmount_flushes_pending_save(tmp_path: Path):
    """Unmounting (e.g. closing a tab or quitting the app) must flush
    pending edits so users don't lose their last typed characters."""
    scratch = tmp_path / ".patchfeld" / "scratch" / "todo.md"

    app = _Host("todo", tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        nb = app.query_one(Notebook)
        nb.text = "draft"
        await pilot.pause(0)
        # Simulate unmount path — the widget should flush before exit.
        nb.on_unmount()

    assert scratch.read_text(encoding="utf-8") == "draft"
