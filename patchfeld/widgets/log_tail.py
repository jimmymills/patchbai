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
        self._inode = None
        self.text = ""
        self._timer = None

    def compose(self) -> ComposeResult:
        yield Static("", id="log-tail-content")

    def on_mount(self) -> None:
        if not self._path.exists():
            self.text = f"File not found: {self._path}"
            self._update_static()
            return
        try:
            lines = self._path.read_text(encoding="utf-8", errors="replace").splitlines()
            self.text = "\n".join(lines[-self._tail_lines:])
        except Exception as e:
            self.text = f"Error reading {self._path}: {e}"
            self._update_static()
            return
        self._update_static()
        self._open_at_end()
        self._timer = self.set_interval(0.25, self._tick)

    def _open_at_end(self) -> None:
        try:
            self._fp = self._path.open("r", encoding="utf-8", errors="replace")
            self._fp.seek(0, 2)
            self._inode = self._path.stat().st_ino
        except Exception:
            self._fp = None
            self._inode = None

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
        """Hand the I/O off to a worker so stat() / read() don't run on
        the asyncio main loop. exclusive=True drops a previous tick that
        hasn't finished yet, which is fine — we'll catch up on the next
        interval. Mirrors the SystemUsage widget's pattern."""
        self.run_worker(
            self._tick_async(), exclusive=True, name="log-tail-tick",
        )

    async def _tick_async(self) -> None:
        import asyncio
        changed = await asyncio.to_thread(self._do_io)
        if changed:
            self._update_static()
            self.scroll_end(animate=False)

    def _do_io(self) -> bool:
        # Detect rotation: if the file's inode changed (or it disappeared
        # and a new one took its place), close the old fp and reopen.
        try:
            current_inode = self._path.stat().st_ino if self._path.exists() else None
        except Exception:
            current_inode = None
        if current_inode != getattr(self, "_inode", None):
            if self._fp is not None:
                try:
                    self._fp.close()
                except Exception:
                    pass
                self._fp = None
            if current_inode is not None:
                self._open_at_end()
                # After rotation, read from the start of the new file so
                # the user doesn't miss the first lines.
                if self._fp is not None:
                    try:
                        self._fp.seek(0, 0)
                    except Exception:
                        pass

        if self._fp is None:
            return False
        new = self._fp.read()
        if not new:
            return False
        self.text = (self.text + "\n" + new).strip("\n")
        return True

    def _update_static(self) -> None:
        # Wrap in Rich Text so arbitrary file content (which may contain
        # bracket sequences that look like markup) renders verbatim.
        from rich.text import Text
        try:
            self.query_one("#log-tail-content", Static).update(Text(self.text))
        except Exception:
            pass

    @classmethod
    def default_border_title(cls, props: dict) -> str:
        file_path = props.get("file_path")
        if file_path:
            return f"Log: {Path(file_path).name}"
        return "Log"
