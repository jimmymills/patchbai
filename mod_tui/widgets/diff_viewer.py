import difflib

from rich.syntax import Syntax
from textual.containers import VerticalScroll
from textual.widgets import Static


def _compute_diff(before: str, after: str) -> str:
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile="before",
            tofile="after",
        )
    )


class DiffViewer(VerticalScroll):
    """Scrollable unified-diff viewer.

    Accepts either a precomputed `diff: str` or a `before` + `after` pair from
    which a unified diff is computed. The result is rendered as syntax-
    highlighted `diff` content.
    """

    DEFAULT_BORDER_TITLE = "Diff"

    DEFAULT_CSS = """
    DiffViewer {
        border: round $surface-lighten-2;
        padding: 0 1;
    }
    """

    def __init__(
        self,
        *,
        diff: str | None = None,
        before: str | None = None,
        after: str | None = None,
    ) -> None:
        super().__init__()
        if diff is None and (before is not None or after is not None):
            diff = _compute_diff(before or "", after or "")
        self.diff_text = diff or ""

    def compose(self):
        if self.diff_text:
            yield Static(Syntax(self.diff_text, "diff", theme="ansi_dark"))
        else:
            yield Static("[dim]No diff to display[/dim]")
