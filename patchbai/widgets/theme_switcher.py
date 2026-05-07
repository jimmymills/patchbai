from textual.binding import Binding
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import Footer, Label, ListItem, ListView

from patchbai.persistence.themes_store import NamedThemesStore


class ThemeSwitcherScreen(ModalScreen[str | None]):
    """Pick a theme. Esc dismisses with None; selecting dismisses with the name."""

    DEFAULT_CSS = """
    ThemeSwitcherScreen {
        align: center middle;
    }
    ThemeSwitcherScreen > Container {
        width: 50%;
        height: 60%;
        border: thick $primary;
        background: $surface;
        padding: 1 2;
    }
    ThemeSwitcherScreen ListView {
        height: 1fr;
    }
    """

    BINDINGS = [Binding("escape", "dismiss_none", "cancel")]

    def __init__(
        self,
        *,
        store: NamedThemesStore,
        available_builtins: list[str],
        active: str,
    ) -> None:
        super().__init__()
        self._store = store
        self._builtins = list(available_builtins)
        self._active = active

    def compose(self):
        items: list[ListItem] = []
        for name in self._store.list():
            label = f"* {name}" if name == self._active else f"  {name}"
            items.append(ListItem(Label(label), name=name))
        if self._builtins:
            items.append(ListItem(Label("─ built-ins ─"), name=None))
        for name in self._builtins:
            label = f"* {name}" if name == self._active else f"  {name}"
            items.append(ListItem(Label(label), name=name))
        with Container():
            yield Label("Load theme:")
            yield ListView(*items)
            yield Footer()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.item.name is None:
            return  # separator row — ignore
        self.dismiss(event.item.name)

    def action_dismiss_none(self) -> None:
        self.dismiss(None)
