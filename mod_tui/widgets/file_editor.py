from pathlib import Path

from textual.widgets import TextArea

from mod_tui.widgets._file_lang import load_text as _load_text


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
