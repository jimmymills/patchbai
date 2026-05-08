from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container, VerticalScroll
from textual.widgets import Static

from patchbai.activity.log import ActivityEntry
from patchbai.events import ActivityLogged


class _ActivityRow(Static):
    """One feed row. Variants come in Task 9; for now this just renders a
    compact single line."""

    DEFAULT_CSS = """
    _ActivityRow {
        height: auto;
        padding: 0 1;
    }
    """

    def __init__(self, entry: ActivityEntry) -> None:
        text = self._format(entry)
        super().__init__(text)
        self.entry = entry
        # Plain attribute mirroring the rendered string. Static stores its
        # content in a private `_renderable` field that isn't part of the
        # public API; consumers (tests, click handlers) read this instead.
        self.text = text

    @staticmethod
    def _format(entry: ActivityEntry) -> str:
        ts = entry.timestamp.strftime("%H:%M:%S")
        return f"[{ts}] {entry.kind:<18} {entry.summary}"


class ActivityFeed(Container):
    """Real Activity Feed. Reads backlog from app.activity_log on mount and
    subscribes to ActivityLogged for live updates. Mode filtering arrives
    in Task 7."""

    DEFAULT_BORDER_TITLE = "Activity"

    DEFAULT_CSS = """
    ActivityFeed {
        border: round $surface-lighten-2;
        padding: 0 1;
    }
    ActivityFeed VerticalScroll {
        height: 1fr;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._unsub = None

    def compose(self) -> ComposeResult:
        yield VerticalScroll(id="activity-rows")

    def on_mount(self) -> None:
        # Tolerate test contexts where the app fixture skipped wiring
        # event_bus / activity_log (mirrors AgentTable's defensive pattern).
        bus = getattr(self.app, "event_bus", None)
        log = getattr(self.app, "activity_log", None)
        if bus is None or log is None:
            return
        scroll = self.query_one("#activity-rows", VerticalScroll)
        for entry in log.entries():
            scroll.mount(_ActivityRow(entry))
        self._unsub = bus.subscribe(ActivityLogged, self._on_logged)

    def on_unmount(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None

    def _on_logged(self, event: ActivityLogged) -> None:
        scroll = self.query_one("#activity-rows", VerticalScroll)
        scroll.mount(_ActivityRow(event.entry))
