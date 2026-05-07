from pathlib import Path

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Markdown as _TxMarkdown


class MarkdownPanel(VerticalScroll):
    """Renders markdown text from `source` or `file_path`, wrapped in a
    VerticalScroll so long documents are scrollable. The internal
    `_markdown` attribute holds the source string for tests.

    The class is named `MarkdownPanel` rather than `Markdown` to avoid
    a CSS type-selector collision with `textual.widgets.Markdown`. Textual's
    Markdown widget declares `Markdown { height: auto; overflow-y: hidden; }`
    in its DEFAULT_CSS; because Textual matches type selectors by class
    `__name__` and (when SCOPED_CSS=True) only prepends the scope when the
    rule's first selector differs from the scope name, those rules would
    otherwise leak onto our outer container — sizing it to its content and
    suppressing the scrollbar. With distinct class names, each widget's
    DEFAULT_CSS stays in its own lane. The public alias `Markdown` below
    preserves the existing import surface and registry name."""

    DEFAULT_CSS = """
    MarkdownPanel {
        border: round $surface-lighten-2;
        padding: 0 1;
    }
    """

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


# Public alias: keep `from patchbai.widgets.markdown import Markdown` working
# and the registry name "Markdown" stable. Aliasing does NOT change the
# class's `__name__` (still "MarkdownPanel"), which is the property Textual
# uses for CSS type-selector matching — so the leak from textual's own
# Markdown rule is avoided.
Markdown = MarkdownPanel
