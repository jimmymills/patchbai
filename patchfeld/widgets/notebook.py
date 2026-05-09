from pathlib import Path

from textual.timer import Timer
from textual.widgets import TextArea


class Notebook(TextArea):
    """Persistent scratch buffer at <cwd>/.patchfeld/scratch/<name>.md.

    Saves are debounced: a keystroke schedules a write 500ms in the
    future and resets that timer if more keystrokes follow, so a burst
    of typing collapses to one disk write at the end of the burst.
    Pending writes are flushed on blur and on unmount so the user can't
    lose recent edits by closing a tab or quitting.
    """

    # How long to wait after the last keystroke before persisting.
    _SAVE_DEBOUNCE_S = 0.5

    DEFAULT_CSS = """
    Notebook {
        border: round $surface-lighten-2;
    }
    """

    def __init__(self, *, name: str) -> None:
        super().__init__("", language="markdown")
        self._name = name
        self._pending_save_timer: Timer | None = None

    def _path(self) -> Path:
        cwd = getattr(self.app, "cwd", Path.cwd())
        return Path(cwd) / ".patchfeld" / "scratch" / f"{self._name}.md"

    def on_mount(self) -> None:
        path = self._path()
        if path.exists():
            try:
                self.text = path.read_text(encoding="utf-8")
            except Exception:
                pass

    def _save(self) -> None:
        path = self._path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.text, encoding="utf-8")

    def _cancel_pending(self) -> None:
        if self._pending_save_timer is not None:
            try:
                self._pending_save_timer.stop()
            except Exception:
                pass
            self._pending_save_timer = None

    def flush_pending_save(self) -> None:
        """Cancel any in-flight debounce and persist immediately."""
        if self._pending_save_timer is None:
            return
        self._cancel_pending()
        self._save()

    def on_text_area_changed(self, _event) -> None:
        self._cancel_pending()
        self._pending_save_timer = self.set_timer(
            self._SAVE_DEBOUNCE_S, self._on_debounce_fired,
        )

    def _on_debounce_fired(self) -> None:
        self._pending_save_timer = None
        self._save()

    def on_blur(self, _event=None) -> None:
        # User moved focus away — persist immediately so the next thing
        # they do (open a file, run a command, quit) doesn't lose edits.
        self.flush_pending_save()

    def on_unmount(self) -> None:
        # Belt-and-suspenders: even if blur didn't fire, unmounting must
        # not leave debounced edits unsaved.
        self.flush_pending_save()

    @classmethod
    def default_border_title(cls, props: dict) -> str:
        name = props.get("name")
        if name:
            return f"Note: {name}"
        return "Note"
