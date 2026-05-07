import time

from textual.binding import Binding
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Label

from mod_tui.persistence.orchestrator_sessions import OrchestratorSessionsIndex


class ResumeScreen(ModalScreen[str | None]):
    """Pick a past orchestrator session. Esc dismisses with None;
    Enter dismisses with the selected session_id."""

    DEFAULT_CSS = """
    ResumeScreen {
        align: center middle;
    }
    ResumeScreen > Container {
        width: 80%;
        height: 70%;
        border: thick $primary;
        background: $surface;
        padding: 1 2;
    }
    ResumeScreen DataTable {
        height: 1fr;
    }
    """

    BINDINGS = [
        Binding("escape", "dismiss_none", "cancel"),
        Binding("enter", "select", "resume"),
    ]

    def __init__(self, *, index: OrchestratorSessionsIndex) -> None:
        super().__init__()
        self._index = index
        self._ordered_ids: list[str] = []

    def compose(self):
        with Container():
            yield Label("Resume orchestrator session:")
            yield DataTable(cursor_type="row")
            yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns("when", "first message", "turns", "tokens", "id")
        entries = sorted(self._index.list(), key=lambda e: e.last_activity, reverse=True)
        now = time.time()
        for e in entries:
            table.add_row(
                _relative_time(now - e.last_activity),
                _truncate(e.first_user_message or "(no first message)", 60),
                str(e.num_turns),
                f"{e.tokens_in}/{e.tokens_out}",
                e.session_id,
                key=e.session_id,
            )
            self._ordered_ids.append(e.session_id)
        table.focus()

    def _row_session_ids(self) -> list[str]:
        return list(self._ordered_ids)

    def action_dismiss_none(self) -> None:
        self.dismiss(None)

    def action_select(self) -> None:
        table = self.query_one(DataTable)
        if table.row_count == 0:
            self.dismiss(None)
            return
        row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
        self.dismiss(str(row_key.value))

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        # Stop the event so the App's global on_data_table_row_selected
        # handler doesn't also fire and open a TranscriptScreen with our
        # session_id (which isn't a real agent_id).
        event.stop()
        self.dismiss(str(event.row_key.value))


def _relative_time(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)}s ago"
    if seconds < 3600:
        return f"{int(seconds / 60)}m ago"
    if seconds < 86400:
        return f"{int(seconds / 3600)}h ago"
    return f"{int(seconds / 86400)}d ago"


def _truncate(s: str, n: int) -> str:
    if len(s) <= n:
        return s
    return s[: n - 1] + "…"


