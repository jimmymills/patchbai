import pyte
from rich.style import Style
from rich.text import Text

from patchfeld.widgets._terminal_render import (
    _color_to_rich,
    cell_style,
    render_screen,
)


def _feed(text: str, cols: int = 80, rows: int = 24) -> pyte.Screen:
    screen = pyte.Screen(cols, rows)
    stream = pyte.Stream(screen)
    stream.feed(text)
    return screen


def _color_name(span: object) -> str | None:
    color = getattr(getattr(span, "style", None), "color", None)
    return getattr(color, "name", None)


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


def test_cell_style_background_color():
    screen = _feed("\x1b[41mr")  # SGR 41 = red background
    style = cell_style(screen.buffer[0][0])
    assert style is not None
    assert style.bgcolor is not None
    assert style.bgcolor.name == "red"


def test_cell_style_strikethrough():
    screen = _feed("\x1b[9mx")  # SGR 9 = strikethrough
    style = cell_style(screen.buffer[0][0])
    assert style is not None
    assert style.strike is True


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
    # Expect exactly one red span covering 0..3 and one green span covering 3..8
    red_spans = [
        s for s in text.spans
        if s.start == 0 and s.end == 3 and _color_name(s) == "red"
    ]
    green_spans = [
        s for s in text.spans
        if s.start == 3 and s.end == 8 and _color_name(s) == "green"
    ]
    assert len(red_spans) == 1, (
        f"expected exactly one red span at 0..3; spans="
        f"{[(s.start, s.end, _color_name(s)) for s in text.spans]}"
    )
    assert len(green_spans) == 1, (
        f"expected exactly one green span at 3..8; spans="
        f"{[(s.start, s.end, _color_name(s)) for s in text.spans]}"
    )


def test_render_screen_cursor_drawn_when_visible():
    screen = _feed("ab")
    # Cursor should be at position (0,2) after writing 'ab' on row 0
    assert screen.cursor.x == 2
    assert screen.cursor.y == 0
    assert not screen.cursor.hidden
    text = render_screen(screen, show_cursor=True)
    line0 = text.plain.splitlines()[0]
    assert len(line0) >= 3
    # A span at offset 2..3 with reverse=True must exist (exactly one cell wide)
    cursor_spans = [
        s for s in text.spans
        if s.start == 2 and s.end == 3
        and getattr(s.style, "reverse", False) is True
    ]
    assert len(cursor_spans) == 1, (
        f"expected exactly one reverse span at 2..3; spans="
        f"{[(s.start, s.end, getattr(s.style, 'reverse', None)) for s in text.spans]}"
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


# ---------------------------------------------------------------------------
# _color_to_rich direct unit tests (Critical 1 regression coverage)
# ---------------------------------------------------------------------------


def test_color_to_rich_default_returns_none():
    assert _color_to_rich("default") is None
    assert _color_to_rich("") is None


def test_color_to_rich_named_red_passes_through():
    assert _color_to_rich("red") == "red"


def test_color_to_rich_pyte_brown_translates_to_yellow():
    # pyte returns 'brown' for SGR 33; Rich does not understand 'brown'.
    assert _color_to_rich("brown") == "yellow"


def test_color_to_rich_pyte_brightred_translates_to_bright_red():
    assert _color_to_rich("brightred") == "bright_red"


def test_color_to_rich_pyte_brightbrown_translates_to_bright_yellow():
    # pyte composes SGR 93 as "bright" + "brown"; without this mapping the
    # styling silently disappears (no crash, but lost color).
    assert _color_to_rich("brightbrown") == "bright_yellow"


def test_render_screen_does_not_drop_sgr_93():
    screen = _feed("\x1b[93mY\x1b[0m")
    text = render_screen(screen, show_cursor=False)
    bright_yellow_spans = [s for s in text.spans if _color_name(s) == "bright_yellow"]
    assert bright_yellow_spans, (
        f"SGR 93 styling was dropped; spans={[(s.start, s.end, _color_name(s)) for s in text.spans]}"
    )


def test_color_to_rich_all_pyte_bright_names_translate():
    for pyte_name, rich_name in [
        ("brightblack", "bright_black"),
        ("brightred", "bright_red"),
        ("brightgreen", "bright_green"),
        ("brightyellow", "bright_yellow"),
        ("brightbrown", "bright_yellow"),
        ("brightblue", "bright_blue"),
        ("brightmagenta", "bright_magenta"),
        ("brightcyan", "bright_cyan"),
        ("brightwhite", "bright_white"),
    ]:
        assert _color_to_rich(pyte_name) == rich_name


def test_color_to_rich_hex_lowercased():
    assert _color_to_rich("64C832") == "#64c832"


def test_color_to_rich_unknown_returns_none():
    assert _color_to_rich("not-a-real-color") is None
    assert _color_to_rich("xyz123") is None


# ---------------------------------------------------------------------------
# Critical-1: end-to-end test that yellow output renders without crashing.
# ---------------------------------------------------------------------------


def test_render_screen_does_not_crash_on_pyte_color_names():
    """SGR 33 (yellow) and bright SGRs must not crash the renderer."""
    screen = _feed("\x1b[33myellow \x1b[91mbrightred\x1b[0m")
    text = render_screen(screen, show_cursor=False)
    yellow_spans = [s for s in text.spans if _color_name(s) == "yellow"]
    bright_red_spans = [s for s in text.spans if _color_name(s) == "bright_red"]
    assert yellow_spans
    assert bright_red_spans


# ---------------------------------------------------------------------------
# Critical-2: cursor visibility on reverse cells.
# ---------------------------------------------------------------------------


def test_render_screen_cursor_visible_on_reverse_cell():
    """Cursor on a cell that already has reverse=True must still be its own run."""
    screen = _feed("\x1b[7mab\x1b[0m")
    # After 'ab', cursor sits at col 2 on a default cell, but the *previous*
    # cells are reverse=True. The cursor span must not coalesce with them.
    text = render_screen(screen, show_cursor=True)
    # Span covering col 2 must be exactly width 1.
    cursor_spans = [s for s in text.spans if s.start <= 2 < s.end]
    assert any(s.end - s.start == 1 for s in cursor_spans), (
        f"cursor not its own run; spans={[(s.start, s.end) for s in cursor_spans]}"
    )


def test_render_screen_cursor_on_reverse_cell_toggles_reverse_off():
    """If cell at cursor already has reverse=True, cursor cell renders with reverse=False so it stands out."""
    screen = pyte.Screen(20, 5)
    stream = pyte.Stream(screen)
    # Set reverse=True, type 'abc', then move cursor to row 1, col 2 (1-indexed)
    stream.feed("\x1b[7mabc\x1b[1;2H")
    # CSI H is 1-indexed: row 1 col 2 = (y=0, x=1)
    assert screen.cursor.x == 1
    assert screen.cursor.y == 0
    assert screen.buffer[0][1].reverse is True
    text = render_screen(screen, show_cursor=True)
    # The cursor cell at col 1 should have reverse=False in its effective style
    cursor_spans = [s for s in text.spans if s.start == 1 and s.end == 2]
    assert cursor_spans, (
        f"expected exactly one span at col 1, got spans="
        f"{[(s.start, s.end) for s in text.spans]}"
    )
    style = cursor_spans[0].style
    # Style is either a Style object or a str; we want reverse=False on the
    # cursor cell when the underlying cell already has reverse=True.
    if isinstance(style, Style):
        assert style.reverse is not True, (
            f"cursor should toggle reverse OFF on a reverse cell; got style={style!r}"
        )


# ---------------------------------------------------------------------------
# Multi-row + multi-color-run coverage
# ---------------------------------------------------------------------------


def test_render_screen_multiple_rows():
    screen = _feed("row0\r\nrow1\r\nrow2")
    text = render_screen(screen, show_cursor=False)
    lines = text.plain.splitlines()
    assert lines[0].startswith("row0")
    assert lines[1].startswith("row1")
    assert lines[2].startswith("row2")


def test_render_screen_three_distinct_color_runs():
    screen = _feed("\x1b[31mAA\x1b[32mBB\x1b[33mCC\x1b[0m")
    text = render_screen(screen, show_cursor=False)
    # Expect three distinct spans for the three color regions
    red = [s for s in text.spans if s.start <= 0 < s.end and _color_name(s) == "red"]
    green = [s for s in text.spans if s.start <= 2 < s.end and _color_name(s) == "green"]
    yellow = [s for s in text.spans if s.start <= 4 < s.end and _color_name(s) == "yellow"]
    assert red and green and yellow, (
        f"expected red+green+yellow runs, got spans="
        f"{[(s.start, s.end, _color_name(s)) for s in text.spans]}"
    )
