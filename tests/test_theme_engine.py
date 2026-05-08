import pytest
from textual.app import App

from patchfeld.theme.engine import apply_theme
from patchfeld.theme.spec import ThemePalette, ThemeSpec


def _spec(primary: str = "#005577", extra_css: str = "") -> ThemeSpec:
    return ThemeSpec(palette=ThemePalette(primary=primary), extra_css=extra_css)


@pytest.mark.asyncio
async def test_apply_theme_registers_and_activates():
    class _Host(App):
        pass

    host = _Host()
    async with host.run_test():
        await apply_theme(host, _spec(primary="#112233"), theme_name="alpha")
        assert host.theme == "patchfeld:alpha"
        assert "patchfeld:alpha" in host.available_themes


@pytest.mark.asyncio
async def test_apply_theme_replaces_existing_registration():
    """Re-applying with the same name must not raise (Textual's
    register_theme would raise on duplicate; engine handles unregister)."""
    class _Host(App):
        pass

    host = _Host()
    async with host.run_test():
        await apply_theme(host, _spec(primary="#111111"), theme_name="alpha")
        # Mutate palette and re-apply with same name.
        await apply_theme(host, _spec(primary="#222222"), theme_name="alpha")
        assert host.theme == "patchfeld:alpha"


@pytest.mark.asyncio
async def test_apply_theme_installs_extra_css_source():
    class _Host(App):
        pass

    host = _Host()
    async with host.run_test():
        await apply_theme(
            host,
            _spec(extra_css="OrchestratorChat { border: round $accent; }"),
            theme_name="alpha",
        )
        keys = list(host.stylesheet.source.keys())
        assert ("patchfeld_theme", "extra_css") in keys


@pytest.mark.asyncio
async def test_apply_theme_swaps_extra_css_source():
    """A second apply must remove the previous extra_css before installing the new one."""
    class _Host(App):
        pass

    host = _Host()
    async with host.run_test():
        await apply_theme(
            host, _spec(extra_css="A { color: $accent; }"),
            theme_name="alpha",
        )
        await apply_theme(
            host, _spec(extra_css="B { color: $accent; }"),
            theme_name="alpha",
        )
        key = ("patchfeld_theme", "extra_css")
        assert key in host.stylesheet.source
        css = host.stylesheet.source[key].content
        assert "B {" in css
        assert "A {" not in css


@pytest.mark.asyncio
async def test_apply_theme_drops_extra_css_when_empty():
    class _Host(App):
        pass

    host = _Host()
    async with host.run_test():
        await apply_theme(
            host, _spec(extra_css="A { color: $accent; }"),
            theme_name="alpha",
        )
        await apply_theme(host, _spec(extra_css=""), theme_name="alpha")
        assert ("patchfeld_theme", "extra_css") not in host.stylesheet.source


@pytest.mark.asyncio
async def test_apply_theme_caches_extra_css_on_app():
    class _Host(App):
        pass

    host = _Host()
    async with host.run_test():
        await apply_theme(
            host, _spec(extra_css="X { color: red; }"),
            theme_name="alpha",
        )
        assert host._active_theme_extra_css == "X { color: red; }"
        await apply_theme(host, _spec(extra_css=""), theme_name="alpha")
        assert host._active_theme_extra_css == ""


@pytest.mark.asyncio
async def test_apply_theme_bad_css_raises_before_mutating_app_theme():
    """Malformed CSS must be rejected before app.theme is reassigned."""
    class _Host(App):
        pass

    host = _Host()
    async with host.run_test():
        original_theme = host.theme
        bad_css = "this is not valid css {{{"
        with pytest.raises(Exception):
            await apply_theme(
                host, _spec(extra_css=bad_css), theme_name="alpha",
            )
        assert host.theme == original_theme
        assert "patchfeld:alpha" not in host.available_themes


