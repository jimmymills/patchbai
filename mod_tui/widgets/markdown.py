from pathlib import Path

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Markdown as _TxMarkdown


class Markdown(VerticalScroll):
    """Renders markdown text from `source` or `file_path`, wrapped in a
    VerticalScroll so long documents are scrollable. The internal
    `_markdown` attribute holds the source string for tests."""

    def __init__(
        self,
        *,
        source: str | None = None,
        file_path: str | None = None,
    ) -> None:
        super().__init__()
        if source is None and file_path is not None:
            try:
                source = Path(file_path).read_text(encoding="utf-8")
            except FileNotFoundError:
                source = f"*File not found: {file_path}*"
            except Exception as e:
                source = f"*Error loading {file_path}: {e}*"
        if source is None:
            source = ""
        self._markdown = source

    def compose(self) -> ComposeResult:
        yield _TxMarkdown(self._markdown)

    @classmethod
    def default_border_title(cls, props: dict) -> str:
        file_path = props.get("file_path")
        if file_path:
            return f"Markdown: {Path(file_path).name}"
        return "Markdown"
