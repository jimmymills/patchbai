from pathlib import Path

from textual.widgets import DirectoryTree


class FileTree(DirectoryTree):
    """Wraps Textual's DirectoryTree with a kw-only `path` prop."""

    def __init__(self, *, path: str) -> None:
        super().__init__(Path(path))
