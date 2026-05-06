from pathlib import Path

from textual.widgets import Markdown as _TxMarkdown


class Markdown(_TxMarkdown):
    """Renders markdown text from `source` or `file_path`. The internal
    `_markdown` attribute holds the source string for tests."""

    def __init__(
        self,
        *,
        source: str | None = None,
        file_path: str | None = None,
    ) -> None:
        if source is None and file_path is not None:
            try:
                source = Path(file_path).read_text(encoding="utf-8")
            except FileNotFoundError:
                source = f"*File not found: {file_path}*"
            except Exception as e:
                source = f"*Error loading {file_path}: {e}*"
        if source is None:
            source = ""
        super().__init__(source)
        self._markdown = source
