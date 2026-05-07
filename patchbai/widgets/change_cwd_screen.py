from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Static


class ChangeCwdScreen(ModalScreen[str | None]):
    """Tiny modal that asks for a new workspace cwd. Dismisses with the
    trimmed string on submit, or None on escape."""

    DEFAULT_CSS = """
    ChangeCwdScreen { align: center middle; }
    ChangeCwdScreen > Vertical {
        width: 70; height: auto; padding: 1 2;
        background: $surface; border: round $primary;
    }
    """

    BINDINGS = [("escape", "cancel", "cancel")]

    def __init__(self, *, initial: str = "") -> None:
        super().__init__()
        self._initial = initial

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Change workspace cwd:")
            yield Input(
                value=self._initial,
                placeholder="e.g., ~/Developer/other-project",
                id="change-cwd-input",
            )

    def on_input_submitted(self, event: Input.Submitted) -> None:
        path = (event.value or "").strip()
        self.dismiss(path or None)

    def action_cancel(self) -> None:
        self.dismiss(None)
