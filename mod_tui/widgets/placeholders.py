from textual.containers import Container
from textual.widgets import Static


class AgentTable(Container):
    """Placeholder. Becomes a real DataTable wired to AgentManager in plan 2."""

    DEFAULT_CSS = """
    AgentTable {
        border: round $surface-lighten-2;
        padding: 0 1;
    }
    """

    def compose(self):
        yield Static("[dim]Agents — none yet[/dim]")


class ActivityFeed(Container):
    """Placeholder. Becomes a real event stream in plan 2."""

    DEFAULT_CSS = """
    ActivityFeed {
        border: round $surface-lighten-2;
        padding: 0 1;
    }
    """

    def compose(self):
        yield Static("[dim]Activity feed — empty[/dim]")
