from pathlib import Path

from textual.widgets import TextArea


class Notebook(TextArea):
    """Persistent scratch buffer at <cwd>/.patchbai/scratch/<name>.md."""

    DEFAULT_CSS = """
    Notebook {
        border: round $surface-lighten-2;
    }
    """

    def __init__(self, *, name: str) -> None:
        super().__init__("", language="markdown")
        self._name = name

    def _path(self) -> Path:
        cwd = getattr(self.app, "cwd", Path.cwd())
        return Path(cwd) / ".patchbai" / "scratch" / f"{self._name}.md"

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

    @classmethod
    def default_border_title(cls, props: dict) -> str:
        name = props.get("name")
        if name:
            return f"Note: {name}"
        return "Note"
