# FileEditor Widget

## Goal

Add a new `FileEditor` widget that mirrors `FileViewer` but allows the user to actually modify and save the file it's pointing at. Like `FileViewer`, it can subscribe to `FileSelected` events on the bus so a `FileTree` panel drives which file is being edited.

The intended common layout is the existing tree-and-viewer pair, but with the editor in place of the viewer:

```jsonc
{"id": "tree",   "widget": "FileTree",   "props": {"path": "."}}
{"id": "editor", "widget": "FileEditor", "props": {"follow_selection": true}}
```

## Non-goals

- **Vim / modal keybindings.** Intentionally deferred to a separate plan. Default `TextArea` bindings only (which are roughly emacs/readline-flavored) plus `Ctrl+S` to save. A `FileEditor`-with-vim plan can layer onto this widget later via an opt-in `vim: true` prop without changing the default behavior.
- **Live file watching / auto-reload.** The editor does not poll the file. External changes are detected only at save time (see "External-change detection" below). If this proves insufficient in practice, a follow-up can add `LogTail`-style polling.
- **Multiple buffers / tabs inside one editor.** One file at a time. Switching files goes through the dirty-switch flow.
- **Auto-save.** Saves are explicit (`Ctrl+S`).
- **Reformatting / linting / language servers.** Out of scope.
- **Creating new files via a "New" command.** A user can still effectively create a file by pointing the editor at a non-existent path (the save will create it), but there is no first-class `:new` UX.

## Design

### 1. Module layout

New file `patchfeld/widgets/file_editor.py` containing the `FileEditor` widget and the two confirmation modals it pushes (`ConfirmDirtySwitchScreen`, `ConfirmOverwriteScreen`).

The extension→language map and `_load_text` helper currently in `file_viewer.py` are extracted into a new module `patchfeld/widgets/_file_lang.py`:

```python
# patchfeld/widgets/_file_lang.py
from pathlib import Path

_EXTENSION_LANGUAGES = { ... }  # moved verbatim

def detect_language(path: Path) -> str | None: ...

def load_text(path: Path) -> tuple[str, str | None]: ...
```

Both `FileViewer` and `FileEditor` import from it. `FileViewer` is updated to use the shared helpers and otherwise stays unchanged behaviorally.

### 2. Class shape

```python
class FileEditor(TextArea):
    DEFAULT_CSS = """
    FileEditor {
        border: round $surface-lighten-2;
    }
    """

    BINDINGS = [
        Binding("ctrl+s", "save", "save", show=False),
    ]

    def __init__(
        self,
        *,
        file_path: str | None = None,
        follow_selection: bool = False,
    ) -> None: ...
```

Constructor signature is intentionally identical to `FileViewer`. `read_only` is `False` (the `TextArea` default). If `file_path` is provided, we eagerly load it at construct time and seed the cached `_loaded_text`/`_loaded_mtime`/`_loaded_size`/`_current_path` so save/dirty tracking work from the first keystroke. If not, those start as `None`/`""` and the editor only becomes "bound" to a path when `follow_selection=True` delivers one.

### 3. State on the instance

```python
self._follow_selection: bool
self._current_path: Path | None        # what we'll save into
self._loaded_text: str                  # contents at load time, for dirty diff
self._loaded_mtime: float | None        # for external-change detection
self._loaded_size: int | None
self._dirty: bool
self._unsub: Callable[[], None]         # bus unsubscribe, no-op by default
```

### 4. Loading a file

`load_file(path: str)` — same shape as `FileViewer.load_file`, plus state-cache updates:

1. Read the file via `_file_lang.load_text(Path(path))`. Same error semantics as `FileViewer`: missing/unreadable yields a placeholder error text but does not raise. Track whether the read succeeded — on the error path, we deliberately do NOT stat the file in step 2.
2. If the read succeeded, stat the file and cache `_loaded_mtime` / `_loaded_size`. If the read failed (file missing or unreadable), set both to `None`. `None` means "we have no baseline to compare against on save" and disables the external-change check.
3. Set `self.text` to the loaded text (which may be the error placeholder) and (best-effort) `self.language`.
4. Update the cached state: `_loaded_text = self.text`, `_current_path = Path(path)`, `_dirty = False`.
5. Refresh the border title (see §6).

Saving with `_loaded_text` set to a placeholder error string (e.g. `"File not found: …"`) is a corner case we accept: if the user types over the error message and saves, the file is created with their typed contents and the placeholder text is treated as if it were the original — meaning a no-op `Ctrl+S` right after a failed load would write the placeholder string to disk. To avoid that surprise, `action_save()` short-circuits when `_loaded_mtime is None AND self.text == self._loaded_text` (nothing to write, never had a real baseline).

### 5. Dirty tracking

Override `on_text_area_changed`:

```python
def on_text_area_changed(self, _event) -> None:
    new_dirty = self.text != self._loaded_text
    if new_dirty != self._dirty:
        self._dirty = new_dirty
        self._refresh_border_title()
```

We compare against the cached `_loaded_text` rather than maintaining a "modified since last keystroke" flag so that typing-then-undoing back to the original content correctly clears dirty state.

### 6. Border title with dirty marker

`default_border_title(props)` returns the clean form (used by the layout engine on first paint):

```python
@classmethod
def default_border_title(cls, props: dict) -> str:
    fp = props.get("file_path")
    return f"Edit: {Path(fp).name}" if fp else "Edit"
```

After mount, the widget mutates `self.border_title` directly through `_refresh_border_title()`:

- `_current_path is None` → `"Edit"`
- clean → `f"Edit: {self._current_path.name}"`
- dirty → `f"Edit: {self._current_path.name} *"`

### 7. Saving (`Ctrl+S`)

`action_save()` is `async` so it can `await self.app.push_screen_wait(...)` for the overwrite prompt. It returns a bool: `True` if the write succeeded, `False` if it was aborted or failed (used by the dirty-switch flow in §8).

1. If `self._current_path is None`, return `False` (nothing to save into).
2. If `_loaded_mtime is None` AND `self.text == self._loaded_text`, return `False` — see the no-baseline corner case in §4.
3. If `_loaded_mtime is not None`, stat the file. On `FileNotFoundError` — meaning a file we loaded has since been deleted — proceed straight to write (we recreate it). If `mtime` or `size` differs from the cached values, `await push_screen_wait(ConfirmOverwriteScreen(...))`. If the result is anything other than `"overwrite"`, return `False`.
4. Ensure parent directories: `path.parent.mkdir(parents=True, exist_ok=True)`.
5. Write `self.text` to `self._current_path` with `encoding="utf-8"`.
6. Re-stat to refresh `_loaded_mtime`/`_loaded_size`. Set `_loaded_text = self.text`, `_dirty = False`. Refresh the border title. Return `True`.

If the write raises (permissions, disk full), set the border title to `f"Edit: {name} (save failed)"` and log via `self.app.log`. The error-flavored title sticks until the next save attempt or the next `_refresh_border_title()` triggered by a dirty-state change. `_dirty` stays `True` so the user can retry.

### 8. `follow_selection` & dirty-switch flow

In `on_mount`, if `follow_selection=True`, subscribe to `FileSelected`:

```python
def on_mount(self) -> None:
    if self._follow_selection:
        bus = getattr(self.app, "event_bus", None)
        if bus is not None:
            self._unsub = bus.subscribe(FileSelected, self._on_file_selected)
```

`on_unmount` calls `self._unsub()`.

`_on_file_selected(event)`:

- If `not self._dirty` or `Path(event.path) == self._current_path`: just `self.load_file(event.path)`.
- Else: `await push_screen_wait(ConfirmDirtySwitchScreen(new_path=event.path))`. Three buttons returning `"save"`, `"discard"`, or `"cancel"`.
  - `"save"` → `await action_save()`. If it returns `True`, then `load_file(event.path)`. If it returns `False` (user cancelled the overwrite prompt, or write errored), do NOT switch — staying on the dirty file is safer than losing edits silently.
  - `"discard"` → `load_file(event.path)` directly; the cached `_loaded_text` is replaced by the new file's content, so `_dirty` resets cleanly.
  - `"cancel"` → no-op; the tree click is effectively rejected.

Because the handler is now async, wrap the `_on_file_selected` body in a worker (`self.run_worker(self._handle_file_selected(event), exclusive=True)`) so the bus subscription stays a sync callable. `exclusive=True` cancels any prior in-flight switch prompt if the user clicks a third file before answering — the prior worker is cancelled, leaving the editor on the original dirty file with no modal.

### 9. Modals

Both modals are tiny `ModalScreen[str]` subclasses that yield a `Static` prompt and a row of `Button`s, returning a string verb (`"save"`, `"discard"`, `"cancel"`, `"overwrite"`). They live next to the widget in `patchfeld/widgets/file_editor.py` because they're trivial and tightly coupled. If they grow, split into `file_editor_modals.py`.

`ConfirmDirtySwitchScreen(new_path: str)`:

> Unsaved changes in `<current name>`. Save & switch to `<new name>`, discard, or cancel?

`ConfirmOverwriteScreen(path: str)`:

> `<name>` was changed on disk since you opened it. Overwrite anyway?

The widget interacts with these via `await self.app.push_screen_wait(modal)`, which is the existing pattern in Textual for screen-driven prompts. The modals also bind `escape` to dismiss with `"cancel"` so users can back out without reaching for the mouse.

### 10. Registration

In `patchfeld/app.py:build_default_registry`:

```python
from patchfeld.widgets.file_editor import FileEditor

reg.register(
    "FileEditor", FileEditor,
    description=(
        "Editable syntax-highlighted file editor. Pass `file_path` for an "
        "initial file. Pass `follow_selection: true` to subscribe to "
        "FileSelected events from a FileTree panel. Ctrl+S saves; the "
        "border title shows ' *' when there are unsaved changes. Prompts "
        "before discarding edits or overwriting external changes."
    ),
    props_schema={"file_path": str, "follow_selection": bool},
)
```

### 11. Tests

New `tests/test_widget_file_editor.py`, mirroring `tests/test_widget_file_viewer.py`:

- `test_file_editor_loads_text_content`
- `test_file_editor_detects_python_language`
- `test_file_editor_missing_file_shows_error`
- `test_file_editor_marks_dirty_after_typing`
- `test_file_editor_clears_dirty_after_save_writes_file`
- `test_file_editor_dirty_marker_in_border_title`
- `test_file_editor_typing_back_to_original_clears_dirty`
- `test_file_editor_save_with_no_path_is_noop`

New `tests/test_widget_file_tree_editor_pair.py`, mirroring the viewer-pair test:

- `test_file_editor_with_follow_selection_loads_clean_event`
- `test_file_editor_with_follow_selection_pushes_modal_when_dirty`
- `test_file_editor_without_follow_selection_ignores_event`

External-change handling has its own test (mutates the file on disk between load and save and asserts the overwrite modal is pushed). Modal acceptance/dismissal can be exercised with `pilot.press` against the buttons.

## Behavior summary

| Action | Result |
|---|---|
| Mount with `file_path=...` | File loaded, clean, border title `Edit: name` |
| Type | `_dirty=True`, border title `Edit: name *` |
| `Ctrl+S` (no external change) | Write file, clean, border title `Edit: name` |
| `Ctrl+S` (file changed on disk) | `ConfirmOverwriteScreen` → write only on `Overwrite` |
| `Ctrl+S` with `_current_path is None` | No-op |
| `FileSelected` event, clean | Load new file |
| `FileSelected` event, dirty, same path | Load new file (refresh) |
| `FileSelected` event, dirty, different path | `ConfirmDirtySwitchScreen` → save / discard / cancel |
| Unmount | Unsubscribe from bus |

## Future work (intentionally separate plans)

- **Vim mode.** Opt-in `vim: true` prop adds NORMAL/INSERT modes, mode indicator in border subtitle, motions, operators, counts. Implemented as a key-event interceptor on top of `FileEditor`.
- **Live file watching.** Periodic stat or fs-events to surface "changed on disk" without waiting for save.
- **Visual / VISUAL-LINE selection, search, registers.** Lives downstream of vim mode.
- **Compose-from-spec persistence.** `LayoutSpec` entries pointing at a `FileEditor` already round-trip through the existing layout-store machinery; nothing widget-specific to do.
