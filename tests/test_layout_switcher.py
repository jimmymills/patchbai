import pytest
from textual.app import App
from textual.widgets import ListView

from patchbai.layout.defaults import dashboard_layout
from patchbai.persistence.layouts_store import NamedLayoutsStore
from patchbai.widgets.layout_switcher import (
    ConfirmDeleteLayoutScreen,
    LayoutSwitcherScreen,
)


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


def test_switcher_declares_d_binding():
    """The picker must expose a `d` keybinding so users discover delete the
    same way they discover archive on AgentTable."""
    from textual.binding import Binding

    keys: set[str] = set()
    for b in LayoutSwitcherScreen.BINDINGS:
        if isinstance(b, Binding):
            keys.add(b.key)
        else:
            keys.add(b[0])
    assert "d" in keys


@pytest.mark.asyncio
async def test_pressing_d_opens_delete_confirmation_modal(tmp_path):
    store = NamedLayoutsStore(global_dir=tmp_path)
    store.save("alpha", dashboard_layout())
    store.save("beta", dashboard_layout())

    class _Host(App):
        async def on_mount(self):
            await self.push_screen(LayoutSwitcherScreen(store=store))

    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        list_view = app.screen.query_one(ListView)
        list_view.focus()
        list_view.index = 0
        await pilot.pause()

        await pilot.press("d")
        await pilot.pause()

        assert isinstance(app.screen, ConfirmDeleteLayoutScreen)
        # The confirmation must reference the layout being deleted so the
        # user sees what they're about to remove.
        assert app.screen.layout_name == "alpha"


@pytest.mark.asyncio
async def test_confirming_delete_removes_file_and_row(tmp_path):
    store = NamedLayoutsStore(global_dir=tmp_path)
    store.save("alpha", dashboard_layout())
    store.save("beta", dashboard_layout())

    class _Host(App):
        async def on_mount(self):
            await self.push_screen(LayoutSwitcherScreen(store=store))

    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        switcher = app.screen
        list_view = switcher.query_one(ListView)
        list_view.focus()
        list_view.index = 0
        await pilot.pause()

        await pilot.press("d")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmDeleteLayoutScreen)

        app.screen.dismiss("delete")
        await pilot.pause()
        await pilot.pause()

        # File on disk is gone.
        assert (tmp_path / "layouts" / "alpha.json").exists() is False
        assert store.list() == ["beta"]
        # Row is gone from the picker.
        remaining = [item.name for item in list_view.children]
        assert remaining == ["beta"]


@pytest.mark.asyncio
async def test_cancelling_delete_leaves_file_and_row(tmp_path):
    store = NamedLayoutsStore(global_dir=tmp_path)
    store.save("alpha", dashboard_layout())
    store.save("beta", dashboard_layout())

    class _Host(App):
        async def on_mount(self):
            await self.push_screen(LayoutSwitcherScreen(store=store))

    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        list_view = app.screen.query_one(ListView)
        list_view.focus()
        list_view.index = 0
        await pilot.pause()

        await pilot.press("d")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmDeleteLayoutScreen)

        app.screen.dismiss("cancel")
        await pilot.pause()
        await pilot.pause()

        # Nothing was removed.
        assert (tmp_path / "layouts" / "alpha.json").exists() is True
        assert store.list() == ["alpha", "beta"]
        remaining = [item.name for item in list_view.children]
        assert remaining == ["alpha", "beta"]


@pytest.mark.asyncio
async def test_pressing_d_with_no_rows_is_noop(tmp_path):
    """An empty picker shouldn't crash or push a modal when `d` is hit."""
    store = NamedLayoutsStore(global_dir=tmp_path)

    class _Host(App):
        async def on_mount(self):
            await self.push_screen(LayoutSwitcherScreen(store=store))

    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        switcher = app.screen
        list_view = switcher.query_one(ListView)
        list_view.focus()
        await pilot.pause()

        await pilot.press("d")
        await pilot.pause()

        # The switcher itself stays on top — no modal was pushed.
        assert app.screen is switcher
        assert not isinstance(app.screen, ConfirmDeleteLayoutScreen)


@pytest.mark.asyncio
async def test_confirm_modal_default_focus_is_cancel(tmp_path):
    """An accidental Enter on the confirmation must NOT delete; the cancel
    button must hold focus by default."""
    store = NamedLayoutsStore(global_dir=tmp_path)
    store.save("alpha", dashboard_layout())

    class _Host(App):
        async def on_mount(self):
            await self.push_screen(LayoutSwitcherScreen(store=store))

    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        list_view = app.screen.query_one(ListView)
        list_view.focus()
        list_view.index = 0
        await pilot.pause()

        await pilot.press("d")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmDeleteLayoutScreen)

        focused = app.focused
        assert focused is not None
        assert focused.id == "cancel"
