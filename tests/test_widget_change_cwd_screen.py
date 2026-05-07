import pytest
from textual.app import App, ComposeResult
from textual.widgets import Input

from patchbai.widgets.change_cwd_screen import ChangeCwdScreen


class _Host(App):
    def __init__(self, initial: str) -> None:
        super().__init__()
        self._initial = initial
        self.result: object = "sentinel"

    def compose(self) -> ComposeResult:
        yield Input()  # focus stub

    async def on_mount(self) -> None:
        def _set(value):
            self.result = value
        await self.push_screen(ChangeCwdScreen(initial=self._initial), _set)


@pytest.mark.asyncio
async def test_change_cwd_screen_prefills_initial(tmp_path):
    app = _Host(initial=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        inp = app.screen.query_one("#change-cwd-input", Input)
        assert inp.value == str(tmp_path)


@pytest.mark.asyncio
async def test_change_cwd_screen_submit_returns_trimmed(tmp_path):
    app = _Host(initial="")
    async with app.run_test() as pilot:
        await pilot.pause()
        inp = app.screen.query_one("#change-cwd-input", Input)
        inp.value = "  " + str(tmp_path) + "  "
        await pilot.press("enter")
        await pilot.pause()
        assert app.result == str(tmp_path)


@pytest.mark.asyncio
async def test_change_cwd_screen_escape_returns_none(tmp_path):
    app = _Host(initial=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert app.result is None
