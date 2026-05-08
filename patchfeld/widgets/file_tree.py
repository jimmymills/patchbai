from pathlib import Path

from textual.widgets import DirectoryTree

from patchfeld.events import FileSelected


class FileTree(DirectoryTree):
    """Wraps Textual's DirectoryTree with a kw-only `path` prop. Publishes a
    `FileSelected` event on the EventBus when the user selects a file —
    other widgets (e.g. `FileViewer(follow_selection=True)`) can react."""

    DEFAULT_CSS = """
    FileTree {
        border: round $surface-lighten-2;
        padding: 0 1;
    }
    """

    def __init__(self, *, path: str) -> None:
        super().__init__(Path(path))

    def on_directory_tree_file_selected(self, event) -> None:
        bus = getattr(self.app, "event_bus", None)
        if bus is not None:
            bus.publish(FileSelected(path=str(event.path)))

    @classmethod
    def default_border_title(cls, props: dict) -> str:
        path = props.get("path")
        if path:
            return f"Files: {path}"
        return "Files"
