from pathlib import Path

from textual.widgets import TextArea


class Notebook(TextArea):
    """Persistent scratch buffer at <cwd>/.mod_tui/scratch/<name>.md."""

    def __init__(self, *, name: str) -> None:
        super().__init__("", language="markdown")
        self._name = name

    def _path(self) -> Path:
        cwd = getattr(self.app, "cwd", Path.cwd())
        return Path(cwd) / ".mod_tui" / "scratch" / f"{self._name}.md"

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

    def on_text_area_changed(self, _event) -> None:
        # Saves on every keystroke. Cheap for a scratchpad-sized file.
        self._save()
