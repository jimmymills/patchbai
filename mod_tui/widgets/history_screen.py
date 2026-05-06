from textual.binding import Binding
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Label

from mod_tui.persistence.agents_index import AgentsIndex


class HistoryScreen(ModalScreen[str | None]):
    """Modal listing every agent in agents.json. Selecting dismisses with the id."""

    DEFAULT_CSS = """
    HistoryScreen {
        align: center middle;
    }
    HistoryScreen > Container {
        width: 75%;
        height: 75%;
        border: thick $primary;
        background: $surface;
        padding: 1 2;
    }
    HistoryScreen DataTable {
        height: 1fr;
    }
    """

    BINDINGS = [Binding("escape", "dismiss_none", "cancel")]

    COLUMNS = ("id", "name", "state", "started", "cost")

    def __init__(self, index: AgentsIndex) -> None:
        super().__init__()
        self._index = index

    def compose(self):
        with Container():
            yield Label("Agent history (Enter to view transcript, Esc to close):")
            table = DataTable(zebra_stripes=True, cursor_type="row")
            for col in self.COLUMNS:
                table.add_column(col, key=col)
            for info in self._index.load():
                table.add_row(
                    info.id,
                    info.name,
                    info.state.value,
                    f"{info.started_at:.0f}",
                    f"${info.cost:.4f}",
                    key=info.id,
                )
            yield table
            yield Footer()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.dismiss(str(event.row_key.value))

    def action_dismiss_none(self) -> None:
        self.dismiss(None)
