import pyte
import pytest
from rich.text import Text

from patchbai.widgets._terminal_render import render_screen, cell_style


def _feed(text: str, cols: int = 80, rows: int = 24) -> pyte.Screen:
    screen = pyte.Screen(cols, rows)
    stream = pyte.Stream(screen)
    stream.feed(text)
    return screen


def test_cell_style_default_returns_none():
    screen = _feed("a")
    style = cell_style(screen.buffer[0][0])
    assert style is None  # plain default cell carries no style


def test_cell_style_named_color_red():
    screen = _feed("\x1b[31mr")
    style = cell_style(screen.buffer[0][0])
    assert style is not None
    assert style.color is not None
    assert style.color.name == "red"


def test_cell_style_truecolor_uses_hex():
    screen = _feed("\x1b[38;2;100;200;50mt")
    style = cell_style(screen.buffer[0][0])
    assert style is not None
    assert style.color is not None
    # Rich normalizes to "#rrggbb"
    assert style.color.triplet is not None
    assert style.color.triplet.hex.lower() == "#64c832"


def test_cell_style_bold_italic_underline_reverse():
    screen = _feed("\x1b[1;3;4;7mx")
    style = cell_style(screen.buffer[0][0])
    assert style is not None
    assert style.bold is True
    assert style.italic is True
    assert style.underline is True
    assert style.reverse is True


def test_render_screen_plain_text_visible():
    screen = _feed("hello")
    text = render_screen(screen, show_cursor=False)
    assert isinstance(text, Text)
    # First line begins with 'hello'
    assert text.plain.splitlines()[0].startswith("hello")


def test_render_screen_preserves_color_run():
    # 'red' in red, then 'green' in green
    screen = _feed("\x1b[31mred\x1b[32mgreen\x1b[0m")
    text = render_screen(screen, show_cursor=False)
    # Span starting at col 0, length 3, should carry red color
    spans_at_0 = [s for s in text.spans if s.start <= 0 < s.end]

    def _color_name(span: object) -> str | None:
        color = getattr(getattr(span, "style", None), "color", None)
        return getattr(color, "name", None)

    assert any(_color_name(s) == "red" for s in spans_at_0)


def test_render_screen_cursor_drawn_when_visible():
    screen = _feed("ab")
    # Cursor should be at position (0,2) after writing 'ab' on row 0
    assert screen.cursor.x == 2
    assert screen.cursor.y == 0
    assert not screen.cursor.hidden
    text = render_screen(screen, show_cursor=True)
    # The cell at row 0, col 2 should have a reverse-style span exactly 1 wide
    line0 = text.plain.splitlines()[0]
    # Position 2 is a space (cursor cell on empty); rendered as reverse
    assert len(line0) >= 3
    # A span at offset 2 with reverse=True must exist
    cursor_offset = 2
    assert any(
        s.start <= cursor_offset < s.end
        and getattr(s.style, "reverse", False) is True
        for s in text.spans
    )


def test_render_screen_cursor_hidden_no_cursor_span():
    screen = _feed("ab")
    screen.cursor.hidden = True
    text = render_screen(screen, show_cursor=True)
    # No span carrying just-reverse at cursor position
    cursor_offset = 2
    rev_spans = [
        s for s in text.spans
        if s.start <= cursor_offset < s.end
        and getattr(s.style, "reverse", False) is True
    ]
    assert rev_spans == []


def test_render_screen_show_cursor_false_omits_cursor():
    screen = _feed("ab")
    text = render_screen(screen, show_cursor=False)
    cursor_offset = 2
    rev_spans = [
        s for s in text.spans
        if s.start <= cursor_offset < s.end
        and getattr(s.style, "reverse", False) is True
    ]
    assert rev_spans == []


def test_render_screen_default_color_default_bg_no_style():
    screen = _feed("a")
    text = render_screen(screen, show_cursor=False)
    # No spans for fully-default cells
    spans_at_0 = [s for s in text.spans if s.start == 0 and s.end == 1]
    assert spans_at_0 == []
