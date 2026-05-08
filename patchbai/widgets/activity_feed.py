from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container, VerticalScroll
from textual.widgets import Static

from patchbai.activity.log import ActivityEntry, ActivityKind
from patchbai.events import ActivityLogged

MODES: tuple[str, ...] = ("audit", "agents", "notifs", "debug")

# Per-mode kind allowlists, derived from the design spec table.
_MODE_KINDS: dict[str, frozenset[str]] = {
    "audit": frozenset({
        ActivityKind.AGENT_SPAWNED, ActivityKind.AGENT_STATE, ActivityKind.AGENT_DONE,
        ActivityKind.AGENT_ASK, ActivityKind.AGENT_NOTIFY, ActivityKind.AGENT_ARCHIVE,
        ActivityKind.ORCH_USER, ActivityKind.ORCH_REPLY, ActivityKind.ORCH_SESSION,
        ActivityKind.LAYOUT_APPLIED, ActivityKind.LAYOUT_FAILED,
        ActivityKind.TAB_ADDED, ActivityKind.TAB_CLOSED,
        ActivityKind.WORKSPACE_CWD,
    }),
    "agents": frozenset({
        ActivityKind.AGENT_SPAWNED, ActivityKind.AGENT_STATE, ActivityKind.AGENT_DONE,
        ActivityKind.AGENT_MESSAGE, ActivityKind.AGENT_ASK, ActivityKind.AGENT_NOTIFY,
        ActivityKind.AGENT_ARCHIVE,
    }),
    "notifs": frozenset({
        ActivityKind.AGENT_DONE, ActivityKind.AGENT_ASK, ActivityKind.AGENT_NOTIFY,
        ActivityKind.LAYOUT_FAILED, ActivityKind.WORKSPACE_CWD,
    }),
    "debug": frozenset({
        ActivityKind.AGENT_SPAWNED, ActivityKind.AGENT_STATE, ActivityKind.AGENT_DONE,
        ActivityKind.AGENT_MESSAGE, ActivityKind.AGENT_TOOL, ActivityKind.AGENT_ASK,
        ActivityKind.AGENT_NOTIFY, ActivityKind.AGENT_ARCHIVE,
        ActivityKind.ORCH_USER, ActivityKind.ORCH_REPLY, ActivityKind.ORCH_SESSION,
        ActivityKind.LAYOUT_APPLIED, ActivityKind.LAYOUT_FAILED,
        ActivityKind.TAB_ADDED, ActivityKind.TAB_CLOSED, ActivityKind.TAB_SWITCHED,
        ActivityKind.WORKSPACE_CWD, ActivityKind.FILE_SELECTED,
    }),
}


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
    """Real Activity Feed. Reads backlog from `app.activity_log` on mount,
    subscribes to `ActivityLogged` for live updates, and renders rows whose
    `kind` is allowed by the current `mode`. Mode is selected via the `mode`
    prop (one of `MODES`); invalid values silently fall back to `"audit"`."""

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

    def __init__(self, *, mode: str | None = None) -> None:
        super().__init__()
        if mode is not None and mode not in _MODE_KINDS:
            mode = None  # silently fall back to default; no invariant break
        self.mode: str = mode or "audit"
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
        allow = _MODE_KINDS[self.mode]
        for entry in log.entries():
            if entry.kind in allow:
                scroll.mount(_ActivityRow(entry))
        self._unsub = bus.subscribe(ActivityLogged, self._on_logged)

    def on_unmount(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None

    def _on_logged(self, event: ActivityLogged) -> None:
        entry: ActivityEntry = event.entry  # type: ignore[assignment]
        if entry.kind not in _MODE_KINDS[self.mode]:
            return
        scroll = self.query_one("#activity-rows", VerticalScroll)
        scroll.mount(_ActivityRow(entry))
