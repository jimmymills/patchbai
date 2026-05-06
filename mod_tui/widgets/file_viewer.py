from pathlib import Path

from textual.widgets import TextArea

from mod_tui.events import FileSelected


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


def _detect_language(path: Path) -> str | None:
    return _EXTENSION_LANGUAGES.get(path.suffix.lower())


def _load_text(path: Path) -> tuple[str, str | None]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        text = f"File not found: {path}"
    except Exception as e:
        text = f"Error loading {path}: {e}"
    return text, _detect_language(path)


class FileViewer(TextArea):
    """Read-only file display with extension-based syntax highlighting.

    If `follow_selection=True`, subscribes to `FileSelected` events on the
    EventBus and reloads to show the selected file. Pair with a `FileTree`
    panel to get a click-a-file → see-its-content workflow:

        {"id": "tree",   "widget": "FileTree",   "props": {"path": "."}}
        {"id": "viewer", "widget": "FileViewer", "props": {"follow_selection": true}}
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
