# FileEditor Widget Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `FileEditor` Textual widget that mirrors `FileViewer` (extension-based syntax highlighting, optional `follow_selection` to subscribe to `FileSelected` events) but allows the user to actually save edits with `Ctrl+S`. Show a dirty marker in the border title, prompt before discarding edits when the tree click switches files, and prompt before overwriting files that changed on disk since load.

**Architecture:** Single `FileEditor(TextArea)` subclass in `patchfeld/widgets/file_editor.py`, paralleling the existing `FileViewer`. Two thin `ModalScreen[str]` subclasses live in the same module (`ConfirmDirtySwitchScreen`, `ConfirmOverwriteScreen`) and return string verbs via `dismiss(...)`. The extension→language map and file-loading helper are extracted out of `file_viewer.py` into a shared `patchfeld/widgets/_file_lang.py` so both widgets use one definition. Bus subscription, `default_border_title`, and registration follow the same shape as the existing `FileViewer`/`FileTree` pair.

**Tech Stack:** Python 3.12, Textual 8.x (`TextArea`, `ModalScreen`, `Binding`), pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-05-07-file-editor-widget-design.md`

---

## File Structure

**Created**

- `patchfeld/widgets/_file_lang.py` — shared `_EXTENSION_LANGUAGES` map plus `detect_language()` and `load_text()` helpers used by both `FileViewer` and `FileEditor`.
- `patchfeld/widgets/file_editor.py` — `FileEditor` widget plus `ConfirmDirtySwitchScreen` and `ConfirmOverwriteScreen` modals.
- `tests/test_widget_file_lang.py` — small unit tests for the extracted helpers.
- `tests/test_widget_file_editor.py` — load/dirty/save/border-title tests for the editor in isolation.
- `tests/test_widget_file_tree_editor_pair.py` — tree+editor pair tests covering `follow_selection` and the dirty-switch modal.

**Modified**

- `patchfeld/widgets/file_viewer.py` — replace its private `_EXTENSION_LANGUAGES`, `_detect_language`, `_load_text` with imports from `_file_lang`. No behavior change.
- `patchfeld/app.py` — register `FileEditor` in `build_default_registry()`.

---

## Task 1: Extract shared file-language helper

**Files:**
- Create: `patchfeld/widgets/_file_lang.py`
- Test: `tests/test_widget_file_lang.py`

The current `file_viewer.py` defines an extension-to-language map and a `_load_text` helper privately. Both `FileViewer` and the new `FileEditor` need them. Extract them into a private sibling module before adding the second widget so we don't ship two copies.

- [ ] **Step 1.1: Write the failing tests**

Create `tests/test_widget_file_lang.py`:

```python
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
```

- [ ] **Step 1.2: Run the tests, confirm failure**

```bash
uv run pytest tests/test_widget_file_lang.py -v
```

Expected: all four tests fail with `ModuleNotFoundError: No module named 'patchfeld.widgets._file_lang'`.

- [ ] **Step 1.3: Create the helper module**

Create `patchfeld/widgets/_file_lang.py`:

```python
from pathlib import Path


_EXTENSION_LANGUAGES = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "javascript",  # TextArea ships JS lexer; TS falls back well.
    ".tsx": "javascript",
    ".json": "json",
    ".html": "html",
    ".css": "css",
    ".md": "markdown",
    ".rs": "rust",
    ".go": "go",
    ".sh": "bash",
    ".bash": "bash",
    ".sql": "sql",
    ".toml": "toml",
    ".yaml": "yaml",
    ".yml": "yaml",
}


def detect_language(path: Path) -> str | None:
    return _EXTENSION_LANGUAGES.get(path.suffix.lower())


def load_text(path: Path) -> tuple[str, str | None]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        text = f"File not found: {path}"
    except Exception as e:
        text = f"Error loading {path}: {e}"
    return text, detect_language(path)
```

- [ ] **Step 1.4: Run the tests, confirm pass**

```bash
uv run pytest tests/test_widget_file_lang.py -v
```

Expected: 4 passed.

- [ ] **Step 1.5: Switch FileViewer to the shared helper**

Edit `patchfeld/widgets/file_viewer.py`. Replace the top of the file (the `_EXTENSION_LANGUAGES` dict, `_detect_language`, and `_load_text`) with an import:

```python
from pathlib import Path

from textual.widgets import TextArea

from patchfeld.events import FileSelected
from patchfeld.widgets._file_lang import detect_language as _detect_language, load_text as _load_text
```

Delete the old `_EXTENSION_LANGUAGES` dict and the two helper functions. Leave the `FileViewer` class untouched — it already calls `_load_text` and `_detect_language` by those names, so the aliased imports keep its body unchanged.

- [ ] **Step 1.6: Run the existing FileViewer tests, confirm still pass**

```bash
uv run pytest tests/test_widget_file_viewer.py tests/test_widget_file_tree_viewer_pair.py -v
```

Expected: all existing FileViewer + pair tests pass.

- [ ] **Step 1.7: Commit**

```bash
git add patchfeld/widgets/_file_lang.py patchfeld/widgets/file_viewer.py tests/test_widget_file_lang.py
git commit -m "refactor(widgets): extract shared file-language helper for FileViewer"
```

---

## Task 2: FileEditor scaffold — class, constructor, eager load, language detection

**Files:**
- Create: `patchfeld/widgets/file_editor.py`
- Test: `tests/test_widget_file_editor.py`

Land the bare class so `file_path=` loads content and language is detected. No saves, no dirty tracking, no bus, no modals yet. This is the equivalent of `FileViewer`'s read-only scaffold but writable.

- [ ] **Step 2.1: Write the failing tests**

Create `tests/test_widget_file_editor.py`:

```python
from pathlib import Path

import pytest
from textual.app import App

from patchfeld.widgets.file_editor import FileEditor


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
```

- [ ] **Step 2.2: Run the tests, confirm failure**

```bash
uv run pytest tests/test_widget_file_editor.py -v
```

Expected: all five fail with `ModuleNotFoundError: No module named 'patchfeld.widgets.file_editor'`.

- [ ] **Step 2.3: Create the module with the scaffold**

Create `patchfeld/widgets/file_editor.py`:

```python
from pathlib import Path

from textual.widgets import TextArea

from patchfeld.widgets._file_lang import load_text as _load_text


class FileEditor(TextArea):
    """Editable, syntax-highlighted file editor.

    Mirrors FileViewer for loading and language detection but is writable
    and (in later tasks) supports Ctrl+S save, dirty tracking, follow_selection,
    and modal prompts on dirty switches / external file changes.
    """

    DEFAULT_CSS = """
    FileEditor {
        border: round $surface-lighten-2;
    }
    """

    def __init__(
        self,
        *,
        file_path: str | None = None,
        follow_selection: bool = False,
    ) -> None:
        if file_path is not None:
            text, language = _load_text(Path(file_path))
        else:
            text, language = "", None
        kwargs: dict = {}
        if language is not None:
            kwargs["language"] = language
        super().__init__(text, **kwargs)
        self._follow_selection = follow_selection
        self._current_path: Path | None = Path(file_path) if file_path else None
        self._loaded_text: str = text
```

- [ ] **Step 2.4: Run the tests, confirm pass**

```bash
uv run pytest tests/test_widget_file_editor.py -v
```

Expected: 5 passed.

- [ ] **Step 2.5: Commit**

```bash
git add patchfeld/widgets/file_editor.py tests/test_widget_file_editor.py
git commit -m "feat(widgets): FileEditor scaffold with file_path load + language detect"
```

---

## Task 3: Dirty tracking and border title with `*` marker

**Files:**
- Modify: `patchfeld/widgets/file_editor.py`
- Test: `tests/test_widget_file_editor.py`

Add `_dirty` state, `default_border_title`, a runtime `_refresh_border_title()` method, and `on_text_area_changed` to flip dirty when text diverges from the loaded baseline. The border title shows `Edit: name` when clean and `Edit: name *` when dirty.

- [ ] **Step 3.1: Add the failing tests**

Append to `tests/test_widget_file_editor.py`:

```python
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
```

- [ ] **Step 3.2: Run the tests, confirm failure**

```bash
uv run pytest tests/test_widget_file_editor.py -v
```

Expected: the five new tests fail (no `is_dirty`, no `default_border_title`, no border-title mutation).

- [ ] **Step 3.3: Implement dirty tracking and the border title**

Edit `patchfeld/widgets/file_editor.py`. Add the `default_border_title` classmethod, an `is_dirty` property, the `_refresh_border_title()` helper, and the `on_text_area_changed` hook.

Replace the entire `FileEditor` class body so far with:

```python
class FileEditor(TextArea):
    """Editable, syntax-highlighted file editor.

    Mirrors FileViewer for loading and language detection but is writable
    and (in later tasks) supports Ctrl+S save, dirty tracking, follow_selection,
    and modal prompts on dirty switches / external file changes.
    """

    DEFAULT_CSS = """
    FileEditor {
        border: round $surface-lighten-2;
    }
    """

    def __init__(
        self,
        *,
        file_path: str | None = None,
        follow_selection: bool = False,
    ) -> None:
        if file_path is not None:
            text, language = _load_text(Path(file_path))
        else:
            text, language = "", None
        kwargs: dict = {}
        if language is not None:
            kwargs["language"] = language
        super().__init__(text, **kwargs)
        self._follow_selection = follow_selection
        self._current_path: Path | None = Path(file_path) if file_path else None
        self._loaded_text: str = text
        self._dirty: bool = False

    @property
    def is_dirty(self) -> bool:
        return self._dirty

    def on_mount(self) -> None:
        self._refresh_border_title()

    def on_text_area_changed(self, _event) -> None:
        new_dirty = self.text != self._loaded_text
        if new_dirty != self._dirty:
            self._dirty = new_dirty
            self._refresh_border_title()

    def _refresh_border_title(self) -> None:
        if self._current_path is None:
            self.border_title = "Edit"
            return
        name = self._current_path.name
        self.border_title = f"Edit: {name} *" if self._dirty else f"Edit: {name}"

    @classmethod
    def default_border_title(cls, props: dict) -> str:
        fp = props.get("file_path")
        return f"Edit: {Path(fp).name}" if fp else "Edit"
```

- [ ] **Step 3.4: Run the tests, confirm pass**

```bash
uv run pytest tests/test_widget_file_editor.py -v
```

Expected: all 10 pass.

- [ ] **Step 3.5: Commit**

```bash
git add patchfeld/widgets/file_editor.py tests/test_widget_file_editor.py
git commit -m "feat(widgets): FileEditor dirty tracking + border title marker"
```

---

## Task 4: `Ctrl+S` save with mtime/size baseline (no overwrite modal yet)

**Files:**
- Modify: `patchfeld/widgets/file_editor.py`
- Test: `tests/test_widget_file_editor.py`

Add the save action without the external-change confirmation modal. We track `_loaded_mtime` / `_loaded_size` from this task on so the next task can layer the modal cleanly. `action_save()` is `async` and returns `bool` per the spec.

- [ ] **Step 4.1: Add the failing tests**

Append to `tests/test_widget_file_editor.py`:

```python
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
```

- [ ] **Step 4.2: Run the tests, confirm failure**

```bash
uv run pytest tests/test_widget_file_editor.py -v
```

Expected: the five new tests fail (no `action_save`, no Ctrl+S binding).

- [ ] **Step 4.3: Implement save**

Edit `patchfeld/widgets/file_editor.py`. Add the `Binding` import, the `BINDINGS` list, the `_loaded_mtime` / `_loaded_size` state in `__init__`, a `_stat_or_none` helper, and the `action_save()` method.

Top of the file:

```python
from pathlib import Path

from textual.binding import Binding
from textual.widgets import TextArea

from patchfeld.widgets._file_lang import load_text as _load_text


def _stat_or_none(path: Path) -> tuple[float, int] | None:
    try:
        st = path.stat()
    except FileNotFoundError:
        return None
    except Exception:
        return None
    return st.st_mtime, st.st_size
```

In the class, add `BINDINGS` right after the `DEFAULT_CSS` block:

```python
    BINDINGS = [
        Binding("ctrl+s", "save", "save", show=False),
    ]
```

Extend `__init__` to seed mtime/size from disk after the load:

```python
    def __init__(
        self,
        *,
        file_path: str | None = None,
        follow_selection: bool = False,
    ) -> None:
        if file_path is not None:
            text, language = _load_text(Path(file_path))
        else:
            text, language = "", None
        kwargs: dict = {}
        if language is not None:
            kwargs["language"] = language
        super().__init__(text, **kwargs)
        self._follow_selection = follow_selection
        self._current_path: Path | None = Path(file_path) if file_path else None
        self._loaded_text: str = text
        self._dirty: bool = False
        if self._current_path is not None and self._current_path.exists():
            stat = _stat_or_none(self._current_path)
            self._loaded_mtime: float | None = stat[0] if stat else None
            self._loaded_size: int | None = stat[1] if stat else None
        else:
            self._loaded_mtime = None
            self._loaded_size = None
```

Add `action_save` near the bottom of the class (after `_refresh_border_title`):

```python
    async def action_save(self) -> bool:
        """Save the current buffer to disk. Returns True iff the file was written."""
        if self._current_path is None:
            return False
        # No-baseline + no-edit short circuit: avoid writing the
        # error-placeholder text after a failed load.
        if self._loaded_mtime is None and self.text == self._loaded_text:
            return False
        try:
            self._current_path.parent.mkdir(parents=True, exist_ok=True)
            self._current_path.write_text(self.text, encoding="utf-8")
        except Exception:
            self.border_title = f"Edit: {self._current_path.name} (save failed)"
            self.app.log("FileEditor save failed", path=str(self._current_path))
            return False
        stat = _stat_or_none(self._current_path)
        if stat is not None:
            self._loaded_mtime, self._loaded_size = stat
        self._loaded_text = self.text
        self._dirty = False
        self._refresh_border_title()
        return True
```

- [ ] **Step 4.4: Run the tests, confirm pass**

```bash
uv run pytest tests/test_widget_file_editor.py -v
```

Expected: all 15 pass.

- [ ] **Step 4.5: Commit**

```bash
git add patchfeld/widgets/file_editor.py tests/test_widget_file_editor.py
git commit -m "feat(widgets): FileEditor Ctrl+S save with dirty clear"
```

---

## Task 5: `ConfirmOverwriteScreen` and external-change detection

**Files:**
- Modify: `patchfeld/widgets/file_editor.py`
- Test: `tests/test_widget_file_editor.py`

When the user presses `Ctrl+S` and the file's mtime/size on disk differs from what we cached at load time, push a small modal that returns `"overwrite"` or `"cancel"`. We don't poll; this fires only at save time.

- [ ] **Step 5.1: Add the failing tests**

Append to `tests/test_widget_file_editor.py`:

```python
import os
import time

from textual.screen import ModalScreen

from patchfeld.widgets.file_editor import ConfirmOverwriteScreen


@pytest.mark.asyncio
async def test_file_editor_external_change_pushes_overwrite_modal(tmp_path: Path):
    p = tmp_path / "foo.py"
    p.write_text("original\n", encoding="utf-8")

    app = _Host(str(p))
    async with app.run_test() as pilot:
        await pilot.pause()
        editor = app.query_one(FileEditor)
        editor.text = "user edits\n"
        await pilot.pause()

        # Mutate the file on disk so size changes from the cached baseline.
        p.write_text("CHANGED ON DISK\n", encoding="utf-8")
        # Bump mtime even on fast filesystems.
        new_mtime = (editor._loaded_mtime or 0.0) + 5.0
        os.utime(p, (new_mtime, new_mtime))

        # Save in a worker so we don't block this coroutine on push_screen_wait.
        result_holder: dict = {}

        async def _do_save() -> None:
            result_holder["result"] = await editor.action_save()

        app.run_worker(_do_save(), exclusive=True)
        await pilot.pause()

        # The overwrite modal should now be on the screen stack.
        assert isinstance(app.screen, ConfirmOverwriteScreen)

        # Cancel: the file on disk must remain the externally-changed text.
        app.screen.dismiss("cancel")
        await pilot.pause()
        await pilot.pause()

        assert result_holder["result"] is False
        assert p.read_text(encoding="utf-8") == "CHANGED ON DISK\n"
        assert editor.is_dirty is True


@pytest.mark.asyncio
async def test_file_editor_external_change_overwrite_writes_file(tmp_path: Path):
    p = tmp_path / "foo.py"
    p.write_text("original\n", encoding="utf-8")

    app = _Host(str(p))
    async with app.run_test() as pilot:
        await pilot.pause()
        editor = app.query_one(FileEditor)
        editor.text = "user edits\n"
        await pilot.pause()

        p.write_text("CHANGED ON DISK\n", encoding="utf-8")
        new_mtime = (editor._loaded_mtime or 0.0) + 5.0
        os.utime(p, (new_mtime, new_mtime))

        result_holder: dict = {}

        async def _do_save() -> None:
            result_holder["result"] = await editor.action_save()

        app.run_worker(_do_save(), exclusive=True)
        await pilot.pause()
        assert isinstance(app.screen, ConfirmOverwriteScreen)
        app.screen.dismiss("overwrite")
        await pilot.pause()
        await pilot.pause()

        assert result_holder["result"] is True
        assert p.read_text(encoding="utf-8") == "user edits\n"
        assert editor.is_dirty is False


@pytest.mark.asyncio
async def test_file_editor_save_recreates_file_deleted_under_us(tmp_path: Path):
    """If the file we loaded was deleted, save should recreate it without prompting."""
    p = tmp_path / "foo.py"
    p.write_text("original\n", encoding="utf-8")

    app = _Host(str(p))
    async with app.run_test() as pilot:
        await pilot.pause()
        editor = app.query_one(FileEditor)
        editor.text = "back from the dead\n"
        await pilot.pause()
        p.unlink()

        result = await editor.action_save()
        await pilot.pause()

        assert result is True
        assert p.read_text(encoding="utf-8") == "back from the dead\n"
```

- [ ] **Step 5.2: Run the tests, confirm failure**

```bash
uv run pytest tests/test_widget_file_editor.py -v
```

Expected: the three new tests fail (`ConfirmOverwriteScreen` does not exist; `action_save` does not consult mtime/size).

- [ ] **Step 5.3: Implement the modal and external-change check**

Edit `patchfeld/widgets/file_editor.py`. Add modal-related imports near the top:

```python
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static
```

At the bottom of the file (below the `FileEditor` class), add:

```python
class ConfirmOverwriteScreen(ModalScreen[str]):
    """Modal shown when Ctrl+S detects the file changed on disk since load.

    Dismisses with one of: 'overwrite', 'cancel'.
    """

    DEFAULT_CSS = """
    ConfirmOverwriteScreen { align: center middle; }
    ConfirmOverwriteScreen > Vertical {
        width: 60; height: auto; padding: 1 2;
        background: $surface; border: round $primary;
    }
    ConfirmOverwriteScreen .row { height: auto; }
    ConfirmOverwriteScreen Button { margin-right: 1; }
    """

    BINDINGS = [("escape", "cancel", "cancel")]

    def __init__(self, *, name: str) -> None:
        super().__init__()
        self._name = name

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(
                f"{self._name} was changed on disk since you opened it. "
                f"Overwrite anyway?"
            )
            yield Static(" ")
            yield Button("Overwrite", id="overwrite", variant="warning")
            yield Button("Cancel", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id or "cancel")

    def action_cancel(self) -> None:
        self.dismiss("cancel")
```

Update `action_save` in `FileEditor` so it consults mtime/size before writing and uses the modal when there's a divergence:

```python
    async def action_save(self) -> bool:
        if self._current_path is None:
            return False
        if self._loaded_mtime is None and self.text == self._loaded_text:
            return False
        if self._loaded_mtime is not None:
            current = _stat_or_none(self._current_path)
            if current is not None and (
                current[0] != self._loaded_mtime
                or current[1] != self._loaded_size
            ):
                verb = await self.app.push_screen_wait(
                    ConfirmOverwriteScreen(name=self._current_path.name)
                )
                if verb != "overwrite":
                    return False
        try:
            self._current_path.parent.mkdir(parents=True, exist_ok=True)
            self._current_path.write_text(self.text, encoding="utf-8")
        except Exception:
            self.border_title = f"Edit: {self._current_path.name} (save failed)"
            self.app.log("FileEditor save failed", path=str(self._current_path))
            return False
        stat = _stat_or_none(self._current_path)
        if stat is not None:
            self._loaded_mtime, self._loaded_size = stat
        self._loaded_text = self.text
        self._dirty = False
        self._refresh_border_title()
        return True
```

- [ ] **Step 5.4: Run the tests, confirm pass**

```bash
uv run pytest tests/test_widget_file_editor.py -v
```

Expected: all 18 pass.

- [ ] **Step 5.5: Commit**

```bash
git add patchfeld/widgets/file_editor.py tests/test_widget_file_editor.py
git commit -m "feat(widgets): FileEditor external-change overwrite confirmation"
```

---

## Task 6: `load_file` method (state-cache-aware reload)

**Files:**
- Modify: `patchfeld/widgets/file_editor.py`
- Test: `tests/test_widget_file_editor.py`

Add `load_file(path)` so an external caller (and Task 7's bus handler) can repoint the editor at a new file. It refreshes text, language, the cached `_loaded_text` / `_loaded_mtime` / `_loaded_size` / `_current_path`, clears dirty, and refreshes the border title.

- [ ] **Step 6.1: Add the failing tests**

Append to `tests/test_widget_file_editor.py`:

```python
@pytest.mark.asyncio
async def test_file_editor_load_file_replaces_buffer_and_path(tmp_path: Path):
    a = tmp_path / "a.py"
    a.write_text("a = 1\n", encoding="utf-8")
    b = tmp_path / "b.py"
    b.write_text("b = 2\n", encoding="utf-8")

    app = _Host(str(a))
    async with app.run_test() as pilot:
        await pilot.pause()
        editor = app.query_one(FileEditor)
        assert editor.text.startswith("a = 1")
        editor.load_file(str(b))
        await pilot.pause()
        assert editor.text.startswith("b = 2")
        assert editor.is_dirty is False
        assert editor.border_title == "Edit: b.py"


@pytest.mark.asyncio
async def test_file_editor_load_file_changes_language(tmp_path: Path):
    a = tmp_path / "a.py"
    a.write_text("a = 1\n", encoding="utf-8")
    b = tmp_path / "b.md"
    b.write_text("# heading\n", encoding="utf-8")

    app = _Host(str(a))
    async with app.run_test() as pilot:
        await pilot.pause()
        editor = app.query_one(FileEditor)
        assert editor.language == "python"
        editor.load_file(str(b))
        await pilot.pause()
        assert editor.language == "markdown"


@pytest.mark.asyncio
async def test_file_editor_load_file_clears_dirty(tmp_path: Path):
    a = tmp_path / "a.py"
    a.write_text("a = 1\n", encoding="utf-8")
    b = tmp_path / "b.py"
    b.write_text("b = 2\n", encoding="utf-8")

    app = _Host(str(a))
    async with app.run_test() as pilot:
        await pilot.pause()
        editor = app.query_one(FileEditor)
        editor.text = "scratch\n"
        await pilot.pause()
        assert editor.is_dirty is True
        editor.load_file(str(b))
        await pilot.pause()
        assert editor.is_dirty is False
        assert editor.text.startswith("b = 2")
```

- [ ] **Step 6.2: Run the tests, confirm failure**

```bash
uv run pytest tests/test_widget_file_editor.py -v
```

Expected: the three new tests fail (`load_file` undefined).

- [ ] **Step 6.3: Implement `load_file`**

Add this method to `FileEditor`, right after `__init__`:

```python
    def load_file(self, file_path: str) -> None:
        path = Path(file_path)
        text, language = _load_text(path)
        self.text = text
        if language is not None:
            try:
                self.language = language
            except Exception:
                pass
        self._current_path = path
        self._loaded_text = text
        if path.exists():
            stat = _stat_or_none(path)
            self._loaded_mtime = stat[0] if stat else None
            self._loaded_size = stat[1] if stat else None
        else:
            self._loaded_mtime = None
            self._loaded_size = None
        self._dirty = False
        self._refresh_border_title()
```

- [ ] **Step 6.4: Run the tests, confirm pass**

```bash
uv run pytest tests/test_widget_file_editor.py -v
```

Expected: all 21 pass.

- [ ] **Step 6.5: Commit**

```bash
git add patchfeld/widgets/file_editor.py tests/test_widget_file_editor.py
git commit -m "feat(widgets): FileEditor.load_file for repointing at new files"
```

---

## Task 7: `ConfirmDirtySwitchScreen` and `follow_selection` bus handler

**Files:**
- Modify: `patchfeld/widgets/file_editor.py`
- Create: `tests/test_widget_file_tree_editor_pair.py`

Subscribe to `FileSelected` when `follow_selection=True`. When a new path is delivered, reload directly if clean; otherwise prompt save / discard / cancel via `ConfirmDirtySwitchScreen`. The handler runs as an `exclusive=True` worker so a third tree click cancels a pending prompt.

- [ ] **Step 7.1: Write the failing tests**

Create `tests/test_widget_file_tree_editor_pair.py`:

```python
from pathlib import Path

import pytest
from textual.app import App

from patchfeld.events import EventBus, FileSelected
from patchfeld.widgets.file_editor import ConfirmDirtySwitchScreen, FileEditor
from patchfeld.widgets.file_tree import FileTree


class _Pair(App):
    def __init__(self, bus: EventBus, root: Path) -> None:
        super().__init__()
        self.event_bus = bus
        self._root = root

    def compose(self):
        yield FileTree(path=str(self._root))
        yield FileEditor(follow_selection=True)


@pytest.mark.asyncio
async def test_file_editor_with_follow_selection_loads_clean_event(tmp_path: Path):
    bus = EventBus()
    target = tmp_path / "hello.py"
    target.write_text("print('hi from editor')\n", encoding="utf-8")

    app = _Pair(bus, tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        editor = app.query_one(FileEditor)
        assert editor.text == ""
        bus.publish(FileSelected(path=str(target)))
        await pilot.pause()
        assert "print('hi from editor')" in editor.text
        assert editor.is_dirty is False


@pytest.mark.asyncio
async def test_file_editor_without_follow_selection_ignores_event(tmp_path: Path):
    bus = EventBus()

    class _Solo(App):
        def __init__(self):
            super().__init__()
            self.event_bus = bus

        def compose(self):
            yield FileEditor()  # default follow_selection=False

    target = tmp_path / "x.py"
    target.write_text("ignored\n", encoding="utf-8")

    app = _Solo()
    async with app.run_test() as pilot:
        await pilot.pause()
        bus.publish(FileSelected(path=str(target)))
        await pilot.pause()
        editor = app.query_one(FileEditor)
        assert "ignored" not in editor.text


@pytest.mark.asyncio
async def test_file_editor_dirty_switch_pushes_modal_then_discard(tmp_path: Path):
    bus = EventBus()
    a = tmp_path / "a.py"
    a.write_text("a = 1\n", encoding="utf-8")
    b = tmp_path / "b.py"
    b.write_text("b = 2\n", encoding="utf-8")

    app = _Pair(bus, tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        editor = app.query_one(FileEditor)
        bus.publish(FileSelected(path=str(a)))
        await pilot.pause()
        editor.text = "a = 999\n"
        await pilot.pause()
        assert editor.is_dirty is True

        bus.publish(FileSelected(path=str(b)))
        await pilot.pause()
        assert isinstance(app.screen, ConfirmDirtySwitchScreen)

        app.screen.dismiss("discard")
        await pilot.pause()
        await pilot.pause()

        assert editor.text.startswith("b = 2")
        assert editor.is_dirty is False


@pytest.mark.asyncio
async def test_file_editor_dirty_switch_cancel_keeps_current_file(tmp_path: Path):
    bus = EventBus()
    a = tmp_path / "a.py"
    a.write_text("a = 1\n", encoding="utf-8")
    b = tmp_path / "b.py"
    b.write_text("b = 2\n", encoding="utf-8")

    app = _Pair(bus, tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        editor = app.query_one(FileEditor)
        bus.publish(FileSelected(path=str(a)))
        await pilot.pause()
        editor.text = "a = 999\n"
        await pilot.pause()

        bus.publish(FileSelected(path=str(b)))
        await pilot.pause()
        assert isinstance(app.screen, ConfirmDirtySwitchScreen)
        app.screen.dismiss("cancel")
        await pilot.pause()
        await pilot.pause()

        assert "999" in editor.text
        assert editor.is_dirty is True


@pytest.mark.asyncio
async def test_file_editor_dirty_switch_save_writes_then_loads(tmp_path: Path):
    bus = EventBus()
    a = tmp_path / "a.py"
    a.write_text("a = 1\n", encoding="utf-8")
    b = tmp_path / "b.py"
    b.write_text("b = 2\n", encoding="utf-8")

    app = _Pair(bus, tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        editor = app.query_one(FileEditor)
        bus.publish(FileSelected(path=str(a)))
        await pilot.pause()
        editor.text = "a = 42\n"
        await pilot.pause()

        bus.publish(FileSelected(path=str(b)))
        await pilot.pause()
        assert isinstance(app.screen, ConfirmDirtySwitchScreen)
        app.screen.dismiss("save")
        await pilot.pause()
        await pilot.pause()

        assert a.read_text(encoding="utf-8") == "a = 42\n"
        assert editor.text.startswith("b = 2")
        assert editor.is_dirty is False


@pytest.mark.asyncio
async def test_file_editor_clean_event_to_same_path_reloads(tmp_path: Path):
    bus = EventBus()
    a = tmp_path / "a.py"
    a.write_text("first\n", encoding="utf-8")

    app = _Pair(bus, tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        editor = app.query_one(FileEditor)
        bus.publish(FileSelected(path=str(a)))
        await pilot.pause()
        assert editor.text == "first\n"

        a.write_text("second\n", encoding="utf-8")
        bus.publish(FileSelected(path=str(a)))
        await pilot.pause()
        assert editor.text == "second\n"
```

- [ ] **Step 7.2: Run the tests, confirm failure**

```bash
uv run pytest tests/test_widget_file_tree_editor_pair.py -v
```

Expected: all six fail (`ConfirmDirtySwitchScreen` undefined; `follow_selection` does not subscribe to the bus).

- [ ] **Step 7.3: Implement the dirty-switch modal and bus handler**

Edit `patchfeld/widgets/file_editor.py`. Add the import for `FileSelected` near the top, alongside the existing imports:

```python
from patchfeld.events import FileSelected
```

Add `_unsub` initialization at the end of `__init__`:

```python
        self._unsub = lambda: None
```

Replace `on_mount` with one that also subscribes to the bus:

```python
    def on_mount(self) -> None:
        self._refresh_border_title()
        if not self._follow_selection:
            return
        bus = getattr(self.app, "event_bus", None)
        if bus is not None:
            self._unsub = bus.subscribe(FileSelected, self._on_file_selected)

    def on_unmount(self) -> None:
        self._unsub()
```

Add the bus handler and the worker that drives the dirty-switch flow. Place these after `load_file`:

```python
    def _on_file_selected(self, event: FileSelected) -> None:
        new_path = Path(event.path)
        if not self._dirty or new_path == self._current_path:
            self.load_file(event.path)
            return
        self.run_worker(
            self._handle_dirty_switch(event.path),
            exclusive=True,
        )

    async def _handle_dirty_switch(self, new_path: str) -> None:
        verb = await self.app.push_screen_wait(
            ConfirmDirtySwitchScreen(
                current_name=self._current_path.name if self._current_path else "",
                new_name=Path(new_path).name,
            )
        )
        if verb == "save":
            saved = await self.action_save()
            if saved:
                self.load_file(new_path)
        elif verb == "discard":
            self.load_file(new_path)
        # cancel: no-op
```

At the bottom of the file, add the modal class next to `ConfirmOverwriteScreen`:

```python
class ConfirmDirtySwitchScreen(ModalScreen[str]):
    """Modal shown when a FileSelected event would discard unsaved edits.

    Dismisses with one of: 'save', 'discard', 'cancel'.
    """

    DEFAULT_CSS = """
    ConfirmDirtySwitchScreen { align: center middle; }
    ConfirmDirtySwitchScreen > Vertical {
        width: 70; height: auto; padding: 1 2;
        background: $surface; border: round $primary;
    }
    ConfirmDirtySwitchScreen Button { margin-right: 1; }
    """

    BINDINGS = [("escape", "cancel", "cancel")]

    def __init__(self, *, current_name: str, new_name: str) -> None:
        super().__init__()
        self._current_name = current_name
        self._new_name = new_name

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(
                f"Unsaved changes in {self._current_name or '(unsaved buffer)'}. "
                f"Save & switch to {self._new_name}, discard, or cancel?"
            )
            yield Static(" ")
            yield Button("Save & Switch", id="save", variant="primary")
            yield Button("Discard & Switch", id="discard", variant="warning")
            yield Button("Cancel", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id or "cancel")

    def action_cancel(self) -> None:
        self.dismiss("cancel")
```

- [ ] **Step 7.4: Run the new tests, confirm pass**

```bash
uv run pytest tests/test_widget_file_tree_editor_pair.py -v
```

Expected: all 6 pass.

- [ ] **Step 7.5: Run the full editor + viewer-pair suite, confirm no regressions**

```bash
uv run pytest tests/test_widget_file_editor.py tests/test_widget_file_lang.py tests/test_widget_file_viewer.py tests/test_widget_file_tree_viewer_pair.py tests/test_widget_file_tree_editor_pair.py -v
```

Expected: every test passes.

- [ ] **Step 7.6: Commit**

```bash
git add patchfeld/widgets/file_editor.py tests/test_widget_file_tree_editor_pair.py
git commit -m "feat(widgets): FileEditor follow_selection + dirty-switch modal"
```

---

## Task 8: Register `FileEditor` in the widget registry

**Files:**
- Modify: `patchfeld/app.py`
- Test: `tests/test_widget_file_editor.py`

Make `FileEditor` available to `LayoutSpec` so users (and the orchestrator's `list_widgets` MCP tool) can drop it into a layout by name.

- [ ] **Step 8.1: Add the failing test**

Append to `tests/test_widget_file_editor.py`:

```python
def test_file_editor_is_registered_in_default_registry():
    from patchfeld.app import build_default_registry
    from patchfeld.widgets.file_editor import FileEditor

    reg = build_default_registry()
    assert "FileEditor" in reg.known()
    assert reg.get("FileEditor") is FileEditor
    info = reg.describe("FileEditor")
    assert "Ctrl+S" in info.description
    assert info.props_schema == {"file_path": str, "follow_selection": bool}
```

- [ ] **Step 8.2: Run the test, confirm failure**

```bash
uv run pytest tests/test_widget_file_editor.py::test_file_editor_is_registered_in_default_registry -v
```

Expected: fails — `'FileEditor' not in reg.known()`.

- [ ] **Step 8.3: Register the widget**

Edit `patchfeld/app.py`. Add the import next to the other widget imports (alphabetical with `file_tree` / `file_viewer`):

```python
from patchfeld.widgets.file_editor import FileEditor
```

In `build_default_registry()`, add the registration directly after the existing `FileViewer` block:

```python
    reg.register(
        "FileEditor", FileEditor,
        description=(
            "Editable syntax-highlighted file editor. Pass `file_path` for "
            "an initial file. Pass `follow_selection: true` to subscribe to "
            "FileSelected events from a FileTree panel. Ctrl+S saves; the "
            "border title shows ' *' when there are unsaved changes. Prompts "
            "before discarding edits or overwriting external changes."
        ),
        props_schema={"file_path": str, "follow_selection": bool},
    )
```

- [ ] **Step 8.4: Run the test, confirm pass**

```bash
uv run pytest tests/test_widget_file_editor.py::test_file_editor_is_registered_in_default_registry -v
```

Expected: passes.

- [ ] **Step 8.5: Run the full suite to confirm no regressions**

```bash
uv run pytest -x -q
```

Expected: every test passes. (Pay attention to the smoke tests in `tests/test_app_smoke*.py` — registering a widget shouldn't affect them, but a regression there is the loudest signal.)

- [ ] **Step 8.6: Commit**

```bash
git add patchfeld/app.py tests/test_widget_file_editor.py
git commit -m "feat(app): register FileEditor in default widget registry"
```
