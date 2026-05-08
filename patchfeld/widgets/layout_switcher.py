from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Label, ListItem, ListView, Static

from patchfeld.persistence.layouts_store import NamedLayoutsStore


class LayoutSwitcherScreen(ModalScreen[str | None]):
    """Pick a saved layout. Esc dismisses with None; selecting dismisses with the name.

    Pressing `d` on a row prompts via :class:`ConfirmDeleteLayoutScreen` and,
    on confirmation, removes the layout's JSON file from disk and the row
    from the picker. The currently-active layout is deleted just like any
    other — the in-memory copy keeps working until the user reloads.
    """

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

    BINDINGS = [
        Binding("escape", "dismiss_none", "cancel"),
        Binding("d", "delete_selected", "delete"),
    ]

    def __init__(self, store: NamedLayoutsStore) -> None:
        super().__init__()
        self._store = store

    def compose(self) -> ComposeResult:
        items = [ListItem(Label(name), name=name) for name in self._store.list()]
        with Container():
            yield Label("Load layout:")
            yield ListView(*items)
            yield Footer()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        self.dismiss(event.item.name)

    def action_dismiss_none(self) -> None:
        self.dismiss(None)

    def action_delete_selected(self) -> None:
        list_view = self.query_one(ListView)
        item = list_view.highlighted_child
        if item is None:
            return
        name = item.name
        if not name:
            return
        # Capture the row's index so we can keep the cursor on the same row
        # number after removal (or clamp to the new last row if we removed it).
        index = list_view.index or 0

        def _on_choice(choice: str | None) -> None:
            if choice != "delete":
                return
            self._store.delete(name)
            # `pop` returns AwaitComplete, which schedules the removal as a
            # task immediately on construction — fire-and-forget is fine here.
            list_view.pop(index)
            new_count = len(list_view) - 1
            if new_count > 0:
                list_view.index = min(index, new_count - 1)
            else:
                list_view.index = None
            self.app.notify(f"Deleted layout '{name}'")

        self.app.push_screen(ConfirmDeleteLayoutScreen(name=name), _on_choice)


class ConfirmDeleteLayoutScreen(ModalScreen[str]):
    """Yes/No confirmation before unlinking a saved layout.

    Dismisses with one of: 'delete', 'cancel'. The Cancel button holds focus
    by default so a stray Enter doesn't destroy data.
    """

    DEFAULT_CSS = """
    ConfirmDeleteLayoutScreen { align: center middle; }
    ConfirmDeleteLayoutScreen > Vertical {
        width: 60; height: auto; padding: 1 2;
        background: $surface; border: round $primary;
    }
    ConfirmDeleteLayoutScreen Button { margin-right: 1; }
    """

    BINDINGS = [Binding("escape", "cancel", "cancel")]

    def __init__(self, *, name: str) -> None:
        super().__init__()
        self.layout_name = name

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(
                f"Delete saved layout '{self.layout_name}'? "
                f"This removes the file from disk; the active layout in "
                f"memory is unaffected until you reload."
            )
            yield Button("Delete", id="delete", variant="error")
            yield Button("Cancel", id="cancel", variant="primary")

    def on_mount(self) -> None:
        # Default focus to Cancel so an accidental Enter doesn't delete.
        self.query_one("#cancel", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id or "cancel")

    def action_cancel(self) -> None:
        self.dismiss("cancel")
