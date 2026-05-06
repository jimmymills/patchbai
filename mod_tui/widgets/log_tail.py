from pathlib import Path

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Static


class LogTail(VerticalScroll):
    """Tails a file: shows existing content, polls every 250ms for additions."""

    DEFAULT_CSS = """
    LogTail {
        border: round $surface-lighten-2;
        padding: 0 1;
    }
    """

    def __init__(self, *, file_path: str, tail_lines: int = 200) -> None:
        super().__init__()
        self._path = Path(file_path)
        self._tail_lines = tail_lines
        self._fp = None
        self.text = ""
        self._timer = None

    def compose(self) -> ComposeResult:
        yield Static("", id="log-tail-content")

    def on_mount(self) -> None:
        if not self._path.exists():
            self.text = f"File not found: {self._path}"
            self._update_static()
            return
        # Read last N lines of existing content.
        try:
            lines = self._path.read_text(encoding="utf-8", errors="replace").splitlines()
            self.text = "\n".join(lines[-self._tail_lines:])
        except Exception as e:
            self.text = f"Error reading {self._path}: {e}"
            self._update_static()
            return
        self._update_static()
        # Open for incremental reads from the end.
        try:
            self._fp = self._path.open("r", encoding="utf-8", errors="replace")
            self._fp.seek(0, 2)  # end
        except Exception:
            self._fp = None
        self._timer = self.set_interval(0.25, self._tick)

    def on_unmount(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        if self._fp is not None:
            try:
                self._fp.close()
            except Exception:
                pass
            self._fp = None

    def _tick(self) -> None:
        if self._fp is None:
            return
        new = self._fp.read()
        if not new:
            return
        self.text = (self.text + "\n" + new).strip("\n")
        self._update_static()
        self.scroll_end(animate=False)

    def _update_static(self) -> None:
        try:
            self.query_one("#log-tail-content", Static).update(self.text)
        except Exception:
            pass
