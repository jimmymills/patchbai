from textual.containers import Container
from textual.widgets import Static


class ActivityFeed(Container):
    """Placeholder. Becomes a real event stream in plan 3."""

    DEFAULT_BORDER_TITLE = "Activity"

    DEFAULT_CSS = """
    ActivityFeed {
        border: round $surface-lighten-2;
        padding: 0 1;
    }
    """

    def compose(self):
        yield Static("[dim]Activity feed — empty[/dim]")
