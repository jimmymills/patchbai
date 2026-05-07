"""Pure helpers that turn a pyte.Screen into a rich.text.Text.

Kept module-private and side-effect-free so they're trivial to unit-test
without spinning up a PTY or a Textual app.
"""

from __future__ import annotations

import pyte
from rich.style import Style
from rich.text import Text

# Rich understands these named colors directly (subset of rich.color.ANSI_COLOR_NAMES
# we expect from pyte after translation below).
_NAMED_COLORS = frozenset({
    "default",
    "black", "red", "green", "yellow", "blue", "magenta", "cyan", "white",
    "bright_black", "bright_red", "bright_green", "bright_yellow",
    "bright_blue", "bright_magenta", "bright_cyan", "bright_white",
})

# pyte uses some color names Rich doesn't recognise (notably "brown" for SGR 33
# and "bright<color>" without an underscore). Translate them to the Rich form
# before pass-through so Style() construction never raises ColorParseError.
_PYTE_TO_RICH_NAME = {
    "brown": "yellow",
    "brightblack": "bright_black",
    "brightred": "bright_red",
    "brightgreen": "bright_green",
    "brightyellow": "bright_yellow",
    "brightblue": "bright_blue",
    "brightmagenta": "bright_magenta",
    "brightcyan": "bright_cyan",
    "brightwhite": "bright_white",
}


def _color_to_rich(color: str) -> str | None:
    """Translate a pyte color string to a Rich color spec, or None for default."""
    if not color or color == "default":
        return None
    # Translate pyte-only names (e.g. 'brown', 'brightred') to their Rich forms.
    if color in _PYTE_TO_RICH_NAME:
        return _PYTE_TO_RICH_NAME[color]
    if color in _NAMED_COLORS:
        return color
    # 6-char hex string (truecolor or 256-color resolved by pyte)
    if len(color) == 6 and all(ch in "0123456789abcdefABCDEF" for ch in color):
        return f"#{color.lower()}"
    # Unknown -- safest is to drop the styling rather than crash.
    return None


def cell_style(cell: pyte.screens.Char) -> Style | None:
    """Return a Rich Style for a pyte cell, or None for fully-default cells."""
    fg = _color_to_rich(cell.fg)
    bg = _color_to_rich(cell.bg)
    bold = bool(cell.bold)
    italic = bool(cell.italics)
    underline = bool(cell.underscore)
    reverse = bool(cell.reverse)
    strike = bool(cell.strikethrough)
    if not (fg or bg or bold or italic or underline or reverse or strike):
        return None
    return Style(
        color=fg,
        bgcolor=bg,
        bold=bold or None,
        italic=italic or None,
        underline=underline or None,
        reverse=reverse or None,
        strike=strike or None,
    )


def render_screen(screen: pyte.Screen, *, show_cursor: bool) -> Text:
    """Render the visible portion of `screen` into a Rich Text.

    Cells with identical styles are coalesced into runs to keep the span
    list small (one cell per character would be O(rows*cols) spans).

    If `show_cursor` is True and the cursor is not hidden, the cell under
    the cursor is rendered as its own one-cell run with reverse XOR'd
    against the underlying cell, so it stays visible even when neighbors
    already carry reverse=True.
    """
    text = Text()
    cols = screen.columns
    rows = screen.lines
    cursor_x = screen.cursor.x
    cursor_y = screen.cursor.y
    cursor_visible = show_cursor and not screen.cursor.hidden

    for y in range(rows):
        if y > 0:
            text.append("\n")
        line_buf = screen.buffer[y]
        run_chars: list[str] = []
        run_style: Style | None = None
        run_started = False
        for x in range(cols):
            cell = line_buf[x]
            base = cell_style(cell)
            is_cursor = cursor_visible and y == cursor_y and x == cursor_x
            if is_cursor:
                # XOR reverse so the cursor cell visually pops even when the
                # underlying cell already has reverse=True.
                base_reverse = bool(base and base.reverse)
                effective = (base or Style()) + Style(reverse=not base_reverse)
            else:
                effective = base
            data = cell.data or " "
            if is_cursor:
                # Always flush before/after the cursor so it cannot coalesce
                # with adjacent cells that happen to share its effective style.
                _flush(text, run_chars, run_style)
                _flush(text, [data], effective)
                run_chars = []
                run_style = None
                run_started = False
            elif not run_started:
                run_chars = [data]
                run_style = effective
                run_started = True
            elif effective == run_style:
                run_chars.append(data)
            else:
                _flush(text, run_chars, run_style)
                run_chars = [data]
                run_style = effective
        _flush(text, run_chars, run_style)
    return text


def _flush(text: Text, chars: list[str], style: Style | None) -> None:
    if not chars:
        return
    chunk = "".join(chars)
    if style is None:
        text.append(chunk)
    else:
        text.append(chunk, style=style)
