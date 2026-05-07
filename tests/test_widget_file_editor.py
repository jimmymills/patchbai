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


@pytest.mark.asyncio
async def test_file_editor_default_border_title_uses_filename():
    title = FileEditor.default_border_title({"file_path": "/tmp/foo.py"})
    assert title == "Edit: foo.py"


@pytest.mark.asyncio
async def test_file_editor_default_border_title_no_path():
    title = FileEditor.default_border_title({})
    assert title == "Edit"


@pytest.mark.asyncio
async def test_file_editor_marks_dirty_after_typing(tmp_path: Path):
    p = tmp_path / "foo.py"
    p.write_text("x = 1\n", encoding="utf-8")

    app = _Host(str(p))
    async with app.run_test() as pilot:
        await pilot.pause()
        editor = app.query_one(FileEditor)
        assert editor.is_dirty is False
        editor.text = "x = 2\n"
        await pilot.pause()
        assert editor.is_dirty is True


@pytest.mark.asyncio
async def test_file_editor_typing_back_to_original_clears_dirty(tmp_path: Path):
    p = tmp_path / "foo.py"
    p.write_text("x = 1\n", encoding="utf-8")

    app = _Host(str(p))
    async with app.run_test() as pilot:
        await pilot.pause()
        editor = app.query_one(FileEditor)
        editor.text = "x = 2\n"
        await pilot.pause()
        assert editor.is_dirty is True
        editor.text = "x = 1\n"
        await pilot.pause()
        assert editor.is_dirty is False


@pytest.mark.asyncio
async def test_file_editor_border_title_shows_dirty_marker(tmp_path: Path):
    p = tmp_path / "foo.py"
    p.write_text("x = 1\n", encoding="utf-8")

    app = _Host(str(p))
    async with app.run_test() as pilot:
        await pilot.pause()
        editor = app.query_one(FileEditor)
        assert editor.border_title == "Edit: foo.py"
        editor.text = "x = 2\n"
        await pilot.pause()
        assert editor.border_title == "Edit: foo.py *"


@pytest.mark.asyncio
async def test_file_editor_save_writes_file_and_clears_dirty(tmp_path: Path):
    p = tmp_path / "foo.py"
    p.write_text("x = 1\n", encoding="utf-8")

    app = _Host(str(p))
    async with app.run_test() as pilot:
        await pilot.pause()
        editor = app.query_one(FileEditor)
        editor.text = "x = 42\n"
        await pilot.pause()
        assert editor.is_dirty is True

        result = await editor.action_save()
        await pilot.pause()

        assert result is True
        assert p.read_text(encoding="utf-8") == "x = 42\n"
        assert editor.is_dirty is False
        assert editor.border_title == "Edit: foo.py"


@pytest.mark.asyncio
async def test_file_editor_save_with_no_path_is_noop():
    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        editor = app.query_one(FileEditor)
        editor.text = "anything\n"
        result = await editor.action_save()
        assert result is False


@pytest.mark.asyncio
async def test_file_editor_save_after_failed_load_skips_when_unchanged(tmp_path: Path):
    """If the initial load failed (placeholder text in buffer) and the user
    pressed Ctrl+S without typing, the placeholder must NOT be written to disk."""
    target = tmp_path / "missing.py"  # never created

    app = _Host(str(target))
    async with app.run_test() as pilot:
        await pilot.pause()
        editor = app.query_one(FileEditor)
        # Buffer is the error placeholder, file does not exist.
        result = await editor.action_save()
        assert result is False
        assert not target.exists()


@pytest.mark.asyncio
async def test_file_editor_save_creates_intermediate_dirs(tmp_path: Path):
    target = tmp_path / "deep" / "nested" / "new.py"
    # Initialize the editor pointing at a not-yet-existing path with content.
    app = _Host(str(target))
    async with app.run_test() as pilot:
        await pilot.pause()
        editor = app.query_one(FileEditor)
        # Type something so we leave the no-baseline-no-change short circuit.
        editor.text = "y = 2\n"
        await pilot.pause()
        result = await editor.action_save()
        assert result is True
        assert target.read_text(encoding="utf-8") == "y = 2\n"


@pytest.mark.asyncio
async def test_file_editor_ctrl_s_binding_triggers_save(tmp_path: Path):
    p = tmp_path / "foo.py"
    p.write_text("x = 1\n", encoding="utf-8")

    app = _Host(str(p))
    async with app.run_test() as pilot:
        await pilot.pause()
        editor = app.query_one(FileEditor)
        editor.focus()
        await pilot.pause()
        editor.text = "x = 99\n"
        await pilot.pause()
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert p.read_text(encoding="utf-8") == "x = 99\n"
        assert editor.is_dirty is False
