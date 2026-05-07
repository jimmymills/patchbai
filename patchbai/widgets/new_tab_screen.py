from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Static


class NewTabScreen(ModalScreen[str | None]):
    """Tiny modal that asks for a tab title and dismisses with the entered
    string (or None on escape)."""

    DEFAULT_CSS = """
    NewTabScreen { align: center middle; }
    NewTabScreen > Vertical {
        width: 50; height: auto; padding: 1 2;
        background: $surface; border: round $primary;
    }
    """

    BINDINGS = [("escape", "cancel", "cancel")]

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("New tab title:")
            yield Input(placeholder="e.g., Logs", id="new-tab-input")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        title = (event.value or "").strip()
        self.dismiss(title or None)

    def action_cancel(self) -> None:
        self.dismiss(None)
