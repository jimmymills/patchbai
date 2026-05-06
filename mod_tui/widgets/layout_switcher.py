from textual.binding import Binding
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import Footer, Label, ListItem, ListView

from mod_tui.persistence.layouts_store import NamedLayoutsStore


class LayoutSwitcherScreen(ModalScreen[str | None]):
    """Pick a saved layout. Esc dismisses with None; selecting dismisses with the name."""

    DEFAULT_CSS = """
    LayoutSwitcherScreen {
        align: center middle;
    }
    LayoutSwitcherScreen > Container {
        width: 50%;
        height: 60%;
        border: thick $primary;
        background: $surface;
        padding: 1 2;
    }
    LayoutSwitcherScreen ListView {
        height: 1fr;
    }
    """

    BINDINGS = [Binding("escape", "dismiss_none", "cancel")]

    def __init__(self, store: NamedLayoutsStore) -> None:
        super().__init__()
        self._store = store

    def compose(self):
        items = [ListItem(Label(name), name=name) for name in self._store.list()]
        with Container():
            yield Label("Load layout:")
            yield ListView(*items)
            yield Footer()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        self.dismiss(event.item.name)

    def action_dismiss_none(self) -> None:
        self.dismiss(None)
