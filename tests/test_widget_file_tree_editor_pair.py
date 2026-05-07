from pathlib import Path

import pytest
from textual.app import App

from patchbai.events import EventBus, FileSelected
from patchbai.widgets.file_editor import ConfirmDirtySwitchScreen, FileEditor
from patchbai.widgets.file_tree import FileTree


class _Pair(App):
    def __init__(self, bus: EventBus, root: Path) -> None:
        super().__init__()
        self.event_bus = bus
        self._root = root

    def compose(self):
        yield FileTree(path=str(self._root))
        yield FileEditor(follow_selection=True)


@pytest.mark.asyncio
async def test_file_editor_with_follow_selection_loads_clean_event(tmp_path: Path):
    bus = EventBus()
    target = tmp_path / "hello.py"
    target.write_text("print('hi from editor')\n", encoding="utf-8")

    app = _Pair(bus, tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        editor = app.query_one(FileEditor)
        assert editor.text == ""
        bus.publish(FileSelected(path=str(target)))
        await pilot.pause()
        assert "print('hi from editor')" in editor.text
        assert editor.is_dirty is False


@pytest.mark.asyncio
async def test_file_editor_without_follow_selection_ignores_event(tmp_path: Path):
    bus = EventBus()

    class _Solo(App):
        def __init__(self):
            super().__init__()
            self.event_bus = bus

        def compose(self):
            yield FileEditor()  # default follow_selection=False

    target = tmp_path / "x.py"
    target.write_text("ignored\n", encoding="utf-8")

    app = _Solo()
    async with app.run_test() as pilot:
        await pilot.pause()
        bus.publish(FileSelected(path=str(target)))
        await pilot.pause()
        editor = app.query_one(FileEditor)
        assert "ignored" not in editor.text


@pytest.mark.asyncio
async def test_file_editor_dirty_switch_pushes_modal_then_discard(tmp_path: Path):
    bus = EventBus()
    a = tmp_path / "a.py"
    a.write_text("a = 1\n", encoding="utf-8")
    b = tmp_path / "b.py"
    b.write_text("b = 2\n", encoding="utf-8")

    app = _Pair(bus, tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        editor = app.query_one(FileEditor)
        bus.publish(FileSelected(path=str(a)))
        await pilot.pause()
        editor.text = "a = 999\n"
        await pilot.pause()
        assert editor.is_dirty is True

        bus.publish(FileSelected(path=str(b)))
        await pilot.pause()
        assert isinstance(app.screen, ConfirmDirtySwitchScreen)

        app.screen.dismiss("discard")
        await pilot.pause()
        await pilot.pause()

        assert editor.text.startswith("b = 2")
        assert editor.is_dirty is False


@pytest.mark.asyncio
async def test_file_editor_dirty_switch_cancel_keeps_current_file(tmp_path: Path):
    bus = EventBus()
    a = tmp_path / "a.py"
    a.write_text("a = 1\n", encoding="utf-8")
    b = tmp_path / "b.py"
    b.write_text("b = 2\n", encoding="utf-8")

    app = _Pair(bus, tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        editor = app.query_one(FileEditor)
        bus.publish(FileSelected(path=str(a)))
        await pilot.pause()
        editor.text = "a = 999\n"
        await pilot.pause()

        bus.publish(FileSelected(path=str(b)))
        await pilot.pause()
        assert isinstance(app.screen, ConfirmDirtySwitchScreen)
        app.screen.dismiss("cancel")
        await pilot.pause()
        await pilot.pause()

        assert "999" in editor.text
        assert editor.is_dirty is True


@pytest.mark.asyncio
async def test_file_editor_dirty_switch_save_writes_then_loads(tmp_path: Path):
    bus = EventBus()
    a = tmp_path / "a.py"
    a.write_text("a = 1\n", encoding="utf-8")
    b = tmp_path / "b.py"
    b.write_text("b = 2\n", encoding="utf-8")

    app = _Pair(bus, tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        editor = app.query_one(FileEditor)
        bus.publish(FileSelected(path=str(a)))
        await pilot.pause()
        editor.text = "a = 42\n"
        await pilot.pause()

        bus.publish(FileSelected(path=str(b)))
        await pilot.pause()
        assert isinstance(app.screen, ConfirmDirtySwitchScreen)
        app.screen.dismiss("save")
        await pilot.pause()
        await pilot.pause()

        assert a.read_text(encoding="utf-8") == "a = 42\n"
        assert editor.text.startswith("b = 2")
        assert editor.is_dirty is False


@pytest.mark.asyncio
async def test_file_editor_clean_event_to_same_path_reloads(tmp_path: Path):
    bus = EventBus()
    a = tmp_path / "a.py"
    a.write_text("first\n", encoding="utf-8")

    app = _Pair(bus, tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        editor = app.query_one(FileEditor)
        bus.publish(FileSelected(path=str(a)))
        await pilot.pause()
        assert editor.text == "first\n"

        a.write_text("second\n", encoding="utf-8")
        bus.publish(FileSelected(path=str(a)))
        await pilot.pause()
        assert editor.text == "second\n"
