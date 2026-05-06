from pathlib import Path

from textual.widgets import TextArea


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


class FileViewer(TextArea):
    """Read-only file display with extension-based syntax highlighting."""

    def __init__(self, *, file_path: str) -> None:
        path = Path(file_path)
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            text = f"File not found: {file_path}"
        except Exception as e:
            text = f"Error loading {file_path}: {e}"
        language = _detect_language(path)
        kwargs: dict = {"read_only": True}
        if language is not None:
            kwargs["language"] = language
        super().__init__(text, **kwargs)
