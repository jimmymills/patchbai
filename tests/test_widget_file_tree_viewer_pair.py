from pathlib import Path

import pytest
from textual.app import App

from patchbai.events import EventBus, FileSelected
from patchbai.widgets.file_tree import FileTree
from patchbai.widgets.file_viewer import FileViewer


class _Pair(App):
    def __init__(self, bus: EventBus, root: Path) -> None:
        super().__init__()
        self.event_bus = bus
        self._root = root

    def compose(self):
        yield FileTree(path=str(self._root))
        yield FileViewer(follow_selection=True)


@pytest.mark.asyncio
async def test_file_tree_publishes_file_selected_on_app_mount(tmp_path: Path):
    """Driving FileTree.on_directory_tree_file_selected publishes FileSelected."""
    bus = EventBus()
    received: list[FileSelected] = []
    bus.subscribe(FileSelected, received.append)

    target = tmp_path / "x.py"
    target.write_text("print('hi')\n")

    app = _Pair(bus, tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        tree = app.query_one(FileTree)

        # Synthesize the DirectoryTree.FileSelected event the way Textual
        # would deliver it, then call the handler directly.
        class _Evt:
            path = target

        tree.on_directory_tree_file_selected(_Evt())
        await pilot.pause()

    assert received == [FileSelected(path=str(target))]


@pytest.mark.asyncio
async def test_file_viewer_with_follow_selection_loads_on_event(tmp_path: Path):
    bus = EventBus()
    target = tmp_path / "hello.py"
    target.write_text("print('hi from viewer')\n")

    app = _Pair(bus, tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        viewer = app.query_one(FileViewer)
        assert viewer.text == ""  # no initial content

        bus.publish(FileSelected(path=str(target)))
        await pilot.pause()

        assert "print('hi from viewer')" in viewer.text


@pytest.mark.asyncio
async def test_file_viewer_without_follow_selection_ignores_event(tmp_path: Path):
    bus = EventBus()

    class _Solo(App):
        def __init__(self):
            super().__init__()
            self.event_bus = bus

        def compose(self):
            yield FileViewer()  # default follow_selection=False

    app = _Solo()
    async with app.run_test() as pilot:
        await pilot.pause()
        viewer = app.query_one(FileViewer)
        assert viewer.text == ""

        target = tmp_path / "x.py"
        target.write_text("ignored\n")
        bus.publish(FileSelected(path=str(target)))
        await pilot.pause()

        assert "ignored" not in viewer.text
