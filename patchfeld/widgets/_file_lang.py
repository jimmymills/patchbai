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
