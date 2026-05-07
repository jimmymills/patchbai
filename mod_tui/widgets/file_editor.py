import logging
from pathlib import Path

from textual.binding import Binding
from textual.widgets import TextArea

from mod_tui.widgets._file_lang import load_text as _load_text

log = logging.getLogger(__name__)


def _stat_or_none(path: Path) -> tuple[float, int] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return st.st_mtime, st.st_size


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

    BINDINGS = [
        Binding("ctrl+s", "save", "save", show=False),
    ]

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
        except OSError:
            self.border_title = f"Edit: {self._current_path.name} (save failed)"
            log.warning("FileEditor save failed: %s", self._current_path)
            return False
        stat = _stat_or_none(self._current_path)
        if stat is not None:
            self._loaded_mtime, self._loaded_size = stat
        self._loaded_text = self.text
        self._dirty = False
        self._refresh_border_title()
        return True

    @classmethod
    def default_border_title(cls, props: dict) -> str:
        fp = props.get("file_path")
        return f"Edit: {Path(fp).name}" if fp else "Edit"
