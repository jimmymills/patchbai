from pathlib import Path

from textual.widgets import TextArea

from mod_tui.events import FileSelected
from mod_tui.widgets._file_lang import detect_language as _detect_language, load_text as _load_text


class FileViewer(TextArea):
    """Read-only file display with extension-based syntax highlighting.

    Loads the entire file into memory at mount time — fine for typical
    source files, but for log-sized content (>~1MB) prefer the LogTail
    widget which streams from the end and polls for additions.

    If `follow_selection=True`, subscribes to `FileSelected` events on the
    EventBus and reloads to show the selected file. Pair with a `FileTree`
    panel to get a click-a-file → see-its-content workflow:

        {"id": "tree",   "widget": "FileTree",   "props": {"path": "."}}
        {"id": "viewer", "widget": "FileViewer", "props": {"follow_selection": true}}
    """

    DEFAULT_CSS = """
    FileViewer {
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
        kwargs: dict = {"read_only": True}
        if language is not None:
            kwargs["language"] = language
        super().__init__(text, **kwargs)
        self._follow_selection = follow_selection
        self._unsub = lambda: None

    def on_mount(self) -> None:
        if not self._follow_selection:
            return
        bus = getattr(self.app, "event_bus", None)
        if bus is None:
            return
        self._unsub = bus.subscribe(FileSelected, self._on_file_selected)

    def on_unmount(self) -> None:
        self._unsub()

    def _on_file_selected(self, event: FileSelected) -> None:
        self.load_file(event.path)

    def load_file(self, file_path: str) -> None:
        path = Path(file_path)
        text, language = _load_text(path)
        self.text = text
        if language is not None:
            try:
                self.language = language
            except Exception:
                pass

    @classmethod
    def default_border_title(cls, props: dict) -> str:
        from pathlib import Path as _P
        file_path = props.get("file_path")
        if file_path:
            return f"File: {_P(file_path).name}"
        return "File"
