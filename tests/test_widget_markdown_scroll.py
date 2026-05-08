"""Regression tests for the Markdown widget's scrollability.

Background: when the patchfeld `Markdown` class shared the CSS type-name
`Markdown` with `textual.widgets.Markdown`, Textual's rule
`Markdown { height: auto; overflow-y: hidden; }` leaked onto the outer
`VerticalScroll`, sizing it to its content rather than to its parent
viewport. Result: `max_scroll_y == 0`, no scrollbar, no key/mouse-wheel
scrolling — the long doc was simply clipped by the panel.

These tests pin down the observable scrolling contract so that contract
can't silently regress."""

import pytest
from textual.app import App, ComposeResult

from patchfeld.widgets.markdown import Markdown


# Long enough to overflow any reasonable terminal-test viewport (24 rows).
_LONG_SOURCE = "\n\n".join(
    f"# Heading {i}\n\nLine A for section {i}.\nLine B for section {i}."
    for i in range(60)
)


class _Host(App):
    """Minimal host app — the Markdown widget IS the only child, so the
    screen's viewport == the widget's container, making overflow assertions
    deterministic regardless of the surrounding chrome."""

    def compose(self) -> ComposeResult:
        yield Markdown(source=_LONG_SOURCE)


@pytest.mark.asyncio
async def test_markdown_panel_is_focusable():
    """A scrollable container is useless without focus; the layout engine
    grants `can_focus=True` to every panel, but the widget class itself
    must not declare `can_focus=False`."""
    app = _Host()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        widget = app.query_one(Markdown)
        assert widget.can_focus is True


@pytest.mark.asyncio
async def test_markdown_panel_constrains_height_to_viewport():
    """The outer container must NOT take its height from its content —
    otherwise it grows past the viewport and there's no overflow to scroll.
    This is the property the CSS-name collision broke."""
    app = _Host()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause(); await pilot.pause()
        widget = app.query_one(Markdown)
        # The widget rendered into a 24-row screen; if its size is the full
        # content height (300+ rows), the outer is sizing to content, not viewport.
        assert widget.size.height <= 24, (
            f"Outer Markdown grew to {widget.size.height} rows — should be "
            f"capped at viewport height (24). Likely an `overflow-y: hidden` / "
            f"`height: auto` style is leaking from textual's Markdown rule."
        )
        # The content is much taller than the viewport, so overflow MUST exist.
        assert widget.virtual_size.height > widget.size.height
        assert widget.max_scroll_y > 0


@pytest.mark.asyncio
async def test_markdown_panel_pagedown_scrolls():
    """PgDn key (bound by ScrollableContainer) must move scroll_y."""
    app = _Host()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause(); await pilot.pause()
        widget = app.query_one(Markdown)
        widget.focus()
        await pilot.pause()
        before = widget.scroll_y
        await pilot.press("pagedown")
        await pilot.pause()
        # Textual scroll_y is a reactive that animates; after pause it should
        # have advanced. Allow any positive delta — exact distance depends on
        # viewport height and is not the contract we care about.
        assert widget.scroll_y > before, (
            f"PgDn did not scroll: scroll_y stayed at {widget.scroll_y}. "
            f"max_scroll_y={widget.max_scroll_y}, "
            f"virtual_size={widget.virtual_size}, size={widget.size}."
        )


@pytest.mark.asyncio
async def test_markdown_panel_mouse_wheel_scrolls():
    """Mouse-wheel events posted to the widget must scroll the content.
    This complements the keyboard test: a user mousing into the panel
    without focusing it should still be able to scroll."""
    from textual import events

    app = _Host()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause(); await pilot.pause()
        widget = app.query_one(Markdown)
        before = widget.scroll_y
        # Synthesize a wheel-down event on the widget. The container's
        # MouseScrollDown handler is what processes mouse-wheel scrolls.
        widget.post_message(
            events.MouseScrollDown(
                widget=widget, x=1, y=1, delta_x=0, delta_y=1,
                button=0, shift=False, meta=False, ctrl=False,
                screen_x=1, screen_y=1, style=widget.rich_style,
            )
        )
        await pilot.pause(); await pilot.pause()
        assert widget.scroll_y > before, (
            f"Mouse wheel did not scroll: scroll_y stayed at {widget.scroll_y}."
        )
