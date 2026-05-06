import pytest
from textual.app import App
from textual.widgets import ListView

from mod_tui.layout.defaults import dashboard_layout
from mod_tui.persistence.layouts_store import NamedLayoutsStore
from mod_tui.widgets.layout_switcher import LayoutSwitcherScreen


@pytest.mark.asyncio
async def test_switcher_lists_saved_names(tmp_path):
    store = NamedLayoutsStore(global_dir=tmp_path)
    store.save("alpha", dashboard_layout())
    store.save("beta", dashboard_layout())

    selected: list[str | None] = []

    class _Host(App):
        async def on_mount(self):
            screen = LayoutSwitcherScreen(store=store)

            def _capture(name):
                selected.append(name)

            await self.push_screen(screen, _capture)

    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        list_view = screen.query_one(ListView)
        items = list(list_view.children)
        assert len(items) == 2
        screen.dismiss("alpha")
        await pilot.pause()

    assert selected == ["alpha"]


@pytest.mark.asyncio
async def test_switcher_dismisses_with_none_on_escape(tmp_path):
    store = NamedLayoutsStore(global_dir=tmp_path)
    store.save("only-one", dashboard_layout())

    selected: list[str | None] = []

    class _Host(App):
        async def on_mount(self):
            await self.push_screen(LayoutSwitcherScreen(store=store), selected.append)

    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()

    assert selected == [None]
