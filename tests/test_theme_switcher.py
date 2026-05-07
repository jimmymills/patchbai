import pytest
from textual.app import App
from textual.widgets import ListView

from patchbai.persistence.themes_store import NamedThemesStore
from patchbai.theme.spec import ThemePalette, ThemeSpec
from patchbai.widgets.theme_switcher import ThemeSwitcherScreen


def _spec() -> ThemeSpec:
    return ThemeSpec(palette=ThemePalette(primary="#005577"))


@pytest.mark.asyncio
async def test_switcher_lists_saved_first_then_builtins(tmp_path):
    store = NamedThemesStore(global_dir=tmp_path)
    store.save("alpha", _spec())
    store.save("beta", _spec())

    selected: list[str | None] = []

    class _Host(App):
        async def on_mount(self):
            screen = ThemeSwitcherScreen(
                store=store,
                available_builtins=["nord", "gruvbox"],
                active="alpha",
            )
            await self.push_screen(screen, selected.append)

    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        list_view = screen.query_one(ListView)
        names = [item.name for item in list_view.children if item.name]
        # Saved come first
        assert names[:2] == ["alpha", "beta"]
        # Built-ins after
        assert "nord" in names
        assert "gruvbox" in names


@pytest.mark.asyncio
async def test_switcher_marks_active_theme(tmp_path):
    store = NamedThemesStore(global_dir=tmp_path)
    store.save("alpha", _spec())

    class _Host(App):
        async def on_mount(self):
            screen = ThemeSwitcherScreen(
                store=store,
                available_builtins=["nord"],
                active="alpha",
            )
            await self.push_screen(screen, lambda _: None)

    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        # Find the alpha ListItem and grab its Label's content.
        alpha_item = next(
            i for i in screen.query_one(ListView).children
            if i.name == "alpha"
        )
        from textual.widgets import Label
        label = next(iter(alpha_item.walk_children(Label)))
        assert "*" in str(label.content)
        assert "alpha" in str(label.content)


@pytest.mark.asyncio
async def test_switcher_dismisses_with_name_on_select(tmp_path):
    store = NamedThemesStore(global_dir=tmp_path)
    store.save("alpha", _spec())

    selected: list[str | None] = []

    class _Host(App):
        async def on_mount(self):
            screen = ThemeSwitcherScreen(
                store=store,
                available_builtins=[],
                active="alpha",
            )
            await self.push_screen(screen, selected.append)

    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.screen.dismiss("alpha")
        await pilot.pause()

    assert selected == ["alpha"]


@pytest.mark.asyncio
async def test_switcher_dismisses_with_none_on_escape(tmp_path):
    store = NamedThemesStore(global_dir=tmp_path)
    store.save("alpha", _spec())

    selected: list[str | None] = []

    class _Host(App):
        async def on_mount(self):
            await self.push_screen(
                ThemeSwitcherScreen(
                    store=store, available_builtins=[], active="alpha",
                ),
                selected.append,
            )

    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()

    assert selected == [None]
