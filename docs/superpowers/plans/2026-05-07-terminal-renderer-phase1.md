# Terminal Widget Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `patchbai.widgets.terminal.Terminal` actually usable for interactive shells — render colors/attributes, support modern keys, propagate resize, integrate with the asyncio event loop, and surface process exit — all against the existing `pyte` engine, no new runtime deps.

**Architecture:** Extract two pure helpers from the widget so they can be unit-tested without a PTY: `_terminal_render.py` (turns a `pyte.Screen` into a `rich.text.Text`) and `_terminal_keys.py` (turns a Textual `Key` event into the bytes a real xterm would send). The widget keeps its lifecycle responsibilities (PTY spawn/teardown, asyncio reader, resize propagation) but delegates rendering and key encoding to those helpers. Switch from `pyte.Screen` to `pyte.HistoryScreen` for scrollback. Replace 50ms polling with `loop.add_reader`.

**Tech Stack:** Python 3.12, Textual 8.2.5, pyte (no version pin change), ptyprocess, Rich (already a Textual dependency). `uv run` for everything. `pytest` (asyncio mode auto). `pyright` for type checks. The plan adds two new modules but no new package dependencies.

**Out of scope (defer to a later branch/spike):**
- libghostty-vt swap (Phase 2 from the research report).
- Sixel / Kitty graphics protocols (cannot be rendered in a Textual cell grid regardless of engine).
- OSC 8 hyperlinks rendered as Textual links — pyte does not parse OSC 8 fully; revisit when we move to libghostty-vt.
- Bracketed paste (`\e[?2004h`).
- Kitty keyboard protocol (DECSET 2017) — also revisit at engine swap.

---

## Pre-flight: branch state and baselines

**You are on branch `terminal-research` in worktree `/Users/jimmy.mills/Developer/patchbai-terminal-research`. Do not push.**

The current `tests/test_widget_terminal.py` has 3 passing tests. Keep them green throughout. After every task: `uv run pytest -x` must pass and `uv run pyright` must report `0 errors, 0 warnings, 0 informations`.

Note: `docs/superpowers/` is gitignored. The plan and report files in that tree must be force-added with `git add -f`.

---

## File structure

**New files:**
- `patchbai/widgets/_terminal_render.py` — pure functions: `render_screen(screen, *, show_cursor) -> rich.text.Text`, `cell_style(cell) -> rich.style.Style | None`, internal `_color_to_rich(c) -> str | None`.
- `patchbai/widgets/_terminal_keys.py` — pure function: `encode_key(key: str, character: str | None) -> bytes | None`. Returns `None` for unhandled keys (caller propagates the event).
- `tests/test_terminal_render.py` — unit tests for the renderer. No app, no PTY.
- `tests/test_terminal_keys.py` — unit tests for the keymap. No app, no PTY.

**Modified files:**
- `patchbai/widgets/terminal.py` — gradually rewritten across Tasks 2–7. Final shape: imports from the two helpers; uses `pyte.HistoryScreen` + `pyte.Stream`; uses `loop.add_reader`; handles `Resize`, `Focus`/`Blur`, expanded `on_key`; appends `[process exited <code>]` on EOF.
- `tests/test_widget_terminal.py` — existing 3 tests stay; new integration tests added per task.

**Total tasks:** 7 substantive + 1 wrap-up.

---

## Task 1: Extract a pure renderer (pyte.Screen → rich.text.Text with full attributes)

**Why first:** Renders are testable without a PTY or event loop. Largest user-visible win. Leaves the existing widget untouched — call site swap comes in Task 2.

**Files:**
- Create: `patchbai/widgets/_terminal_render.py`
- Create: `tests/test_terminal_render.py`

### Background — what pyte gives us

Empirically (verified before writing this plan):

```python
import pyte
s = pyte.Screen(80, 24)
st = pyte.Stream(s)
st.feed("\x1b[31mhello\x1b[0m \x1b[1;4mbold\x1b[0m \x1b[38;2;100;200;50mtrue\x1b[0m")
c = s.buffer[0][0]      # pyte.Char
c.data, c.fg, c.bg, c.bold, c.italics, c.underscore, c.reverse, c.strikethrough
# 'h', 'red', 'default', False, False, False, False, False
```

`fg` / `bg` are strings:
- `"default"` → translate to `None` (let Rich/Textual pick the theme default).
- Named: `"red"`, `"green"`, `"blue"`, `"yellow"`, `"magenta"`, `"cyan"`, `"white"`, `"brown"` — pass through to Rich; Rich understands these names.
- 6-char hex (truecolor or 256-color resolved): `"64c832"` → return `"#64c832"`.
- Bright variants: `"brightred"`, etc. Pass through.

`buffer[y]` is a defaultdict — indexing missing columns returns a default `Char(' ')`, so iterating `range(cols)` is safe.

The cursor: `screen.cursor.x`, `screen.cursor.y`, `screen.cursor.hidden` (bool).

- [ ] **Step 1: Write the failing test**

Create `tests/test_terminal_render.py`:

```python
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
    assert any(
        getattr(s.style, "color", None) is not None
        and s.style.color.name == "red"
        for s in spans_at_0
    )


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_terminal_render.py -v`
Expected: All tests fail with `ModuleNotFoundError: No module named 'patchbai.widgets._terminal_render'`.

- [ ] **Step 3: Write minimal implementation**

Create `patchbai/widgets/_terminal_render.py`:

```python
"""Pure helpers that turn a pyte.Screen into a rich.text.Text.

Kept module-private and side-effect-free so they're trivial to unit-test
without spinning up a PTY or a Textual app.
"""

from __future__ import annotations

import pyte
from rich.style import Style
from rich.text import Text

# pyte returns these named colors as strings; Rich understands them directly.
_NAMED_COLORS = frozenset({
    "default",
    "black", "red", "green", "yellow", "blue", "magenta", "cyan", "white",
    "brown",
    "brightblack", "brightred", "brightgreen", "brightyellow",
    "brightblue", "brightmagenta", "brightcyan", "brightwhite",
})


def _color_to_rich(color: str) -> str | None:
    """Translate a pyte color string to a Rich color spec, or None for default."""
    if not color or color == "default":
        return None
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
    the cursor is rendered with reverse=True (overlaid on its existing style).
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
            effective = ((base or Style()) + Style(reverse=True)) if is_cursor else base
            data = cell.data or " "
            if not run_started:
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_terminal_render.py -v`
Expected: all 9 tests PASS.

If `test_cell_style_truecolor_uses_hex` fails because `style.color.triplet` is `None`: the issue is that Rich resolves the hex color lazily. The assertion may need to compare against the parsed color string. If so, replace the body with:

```python
    assert style.color.name == "#64c832"
```

- [ ] **Step 5: Verify pyright clean**

Run: `uv run pyright patchbai/widgets/_terminal_render.py tests/test_terminal_render.py`
Expected: `0 errors, 0 warnings, 0 informations`.

- [ ] **Step 6: Run full suite to confirm nothing else broke**

Run: `uv run pytest -x`
Expected: all previously-passing tests still pass; new render tests pass.

- [ ] **Step 7: Commit**

```bash
git add patchbai/widgets/_terminal_render.py tests/test_terminal_render.py
git commit -m "feat(terminal): pure renderer mapping pyte cells to rich.Text with attrs"
```

---

## Task 2: Wire the new renderer into the widget; switch to HistoryScreen + pyte.Stream

**Why now:** Replaces the lossy `screen.display` join with the per-cell renderer. Also fixes the encode/decode round-trip and adds scrollback (HistoryScreen). The 50ms polling loop stays for now — Task 3 swaps it for `add_reader`.

**Files:**
- Modify: `patchbai/widgets/terminal.py` (lines 9, 64–66, 109–117, 119–126)
- Modify: `tests/test_widget_terminal.py` (add new test)

- [ ] **Step 1: Write the failing integration test**

Append to `tests/test_widget_terminal.py`:

```python
@pytest.mark.asyncio
async def test_terminal_renders_with_color_attributes(tmp_path):
    # printf "\x1b[31mRED\x1b[0m" — the screen should carry a red span.
    app = _Host(command=["/bin/sh", "-c", "printf '\\033[31mRED\\033[0m'"])
    async with app.run_test() as pilot:
        await pilot.pause()
        term = app.query_one(Terminal)
        for _ in range(20):
            term._tick()
            await pilot.pause()
        from rich.text import Text
        screen_widget = term.query_one("#terminal-screen")
        rendered = screen_widget.renderable
        assert isinstance(rendered, Text)
        # At least one span must carry red coloring across cells [0..3)
        red_spans = [
            s for s in rendered.spans
            if s.start < 3
            and getattr(s.style, "color", None) is not None
            and s.style.color.name == "red"
        ]
        assert red_spans, f"expected a red span, got spans={rendered.spans!r}"
        term._teardown()


@pytest.mark.asyncio
async def test_terminal_uses_history_screen():
    import pyte
    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        term = app.query_one(Terminal)
        assert isinstance(term._screen, pyte.HistoryScreen), \
            f"expected HistoryScreen, got {type(term._screen).__name__}"
        term._teardown()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_widget_terminal.py::test_terminal_renders_with_color_attributes tests/test_widget_terminal.py::test_terminal_uses_history_screen -v`
Expected: both fail. The first fails because the current renderer does `Text("\n".join(self._screen.display))` (no spans). The second fails because the screen is a `pyte.Screen`, not `pyte.HistoryScreen`.

- [ ] **Step 3: Edit `patchbai/widgets/terminal.py`**

Replace these sections.

(a) Imports — add the helper import. Replace lines 1–9 with:

```python
import os
import select

from textual.app import ComposeResult
from textual.containers import Container
from textual.widgets import Static

import ptyprocess
import pyte

from patchbai.widgets._terminal_render import render_screen
```

(b) Constructor — switch screen + stream. Replace lines 63–66:

```python
        self._pty = None
        self._screen = pyte.HistoryScreen(self.DEFAULT_COLS, self.DEFAULT_ROWS, history=2000, ratio=0.5)
        self._stream = pyte.Stream(self._screen)
        self._timer = None
```

(c) `_tick` — drop the encode round-trip. Replace lines 101–117:

```python
    def _tick(self) -> None:
        if self._pty is None:
            return
        try:
            ready, _, _ = select.select([self._pty.fd], [], [], 0)
            if not ready:
                return
            chunk = self._pty.read(1024)
        except EOFError:
            self._teardown()
            return
        except Exception:
            return
        if chunk:
            # PtyProcessUnicode already decoded; pyte.Stream consumes str.
            self._stream.feed(chunk)
            self._refresh()
```

(d) `_refresh` — call the new renderer. Replace lines 119–126:

```python
    def _refresh(self) -> None:
        try:
            screen = self.query_one("#terminal-screen", Static)
        except Exception:
            return
        text = render_screen(self._screen, show_cursor=True)
        screen.update(text)
```

Also delete the now-unused `from rich.text import Text` import inside `_refresh` (it's no longer needed there). Keep the one inside `_show_error` — that one still uses Text directly.

- [ ] **Step 4: Run new tests to verify they pass**

Run: `uv run pytest tests/test_widget_terminal.py::test_terminal_renders_with_color_attributes tests/test_widget_terminal.py::test_terminal_uses_history_screen -v`
Expected: both PASS.

- [ ] **Step 5: Run full suite + pyright**

Run: `uv run pytest -x` → all pass. Run: `uv run pyright` → 0 errors.

- [ ] **Step 6: Commit**

```bash
git add patchbai/widgets/terminal.py tests/test_widget_terminal.py
git commit -m "feat(terminal): render pyte attrs via Text; HistoryScreen + Stream"
```

---

## Task 3: Replace 50ms polling with asyncio add_reader

**Why now:** The renderer is in place; this fixes latency and CPU without changing what's rendered.

**Files:**
- Modify: `patchbai/widgets/terminal.py` — `on_mount`, `_teardown`, drop `_tick`'s timer-driven nature in favor of an fd-readiness callback. Keep `_tick` as the drain function so the existing tests (which call `term._tick()` directly) continue to work.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_widget_terminal.py`:

```python
@pytest.mark.asyncio
async def test_terminal_uses_add_reader_not_timer():
    """on_mount should register an fd reader on the asyncio loop, not a timer."""
    import asyncio
    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        term = app.query_one(Terminal)
        loop = asyncio.get_running_loop()
        # Reader is registered against the PTY fd
        assert term._pty is not None
        # Internal flag we set when add_reader was used:
        assert getattr(term, "_reader_registered", False) is True
        term._teardown()
        # After teardown the reader is gone.
        assert getattr(term, "_reader_registered", False) is False


@pytest.mark.asyncio
async def test_terminal_drains_via_add_reader(tmp_path):
    """Output appears without anyone calling _tick manually."""
    import asyncio
    app = _Host(command=["/bin/sh", "-c", "printf hello-async; sleep 0.3"])
    async with app.run_test() as pilot:
        await pilot.pause()
        term = app.query_one(Terminal)
        # Give the loop time to drain stdout via add_reader, no _tick calls.
        for _ in range(20):
            await asyncio.sleep(0.02)
            await pilot.pause()
        text = "\n".join(term._screen.display)
        assert "hello-async" in text
        term._teardown()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_widget_terminal.py::test_terminal_uses_add_reader_not_timer tests/test_widget_terminal.py::test_terminal_drains_via_add_reader -v`
Expected: both fail. First because `_reader_registered` doesn't exist; second because output won't appear without `term._tick()` calls.

- [ ] **Step 3: Edit `patchbai/widgets/terminal.py`**

(a) Add `import asyncio` at the top (after `import select`).

(b) Add a `_reader_registered: bool = False` field initialization in `__init__` (after `self._timer = None`).

(c) Replace `on_mount` (lines 71–82) with:

```python
    def on_mount(self) -> None:
        try:
            self._pty = ptyprocess.PtyProcessUnicode.spawn(
                self._command,
                cwd=self._cwd,
                env=self._env,
                dimensions=(self.DEFAULT_ROWS, self.DEFAULT_COLS),
            )
        except Exception as e:
            self._show_error(f"PTY spawn failed: {e}")
            return
        loop = asyncio.get_running_loop()
        loop.add_reader(self._pty.fd, self._tick)
        self._reader_registered = True
```

(d) Replace `_teardown` (lines 87–99) with:

```python
    def _teardown(self) -> None:
        if self._reader_registered and self._pty is not None:
            try:
                asyncio.get_running_loop().remove_reader(self._pty.fd)
            except Exception:
                pass
            self._reader_registered = False
        if self._timer is not None:
            try:
                self._timer.stop()
            except Exception:
                pass
            self._timer = None
        if self._pty is not None:
            try:
                self._pty.close(force=True)
            except Exception:
                pass
            self._pty = None
```

(e) Update `_tick` to drain in a loop (since add_reader fires once per readable):

```python
    def _tick(self) -> None:
        if self._pty is None:
            return
        # Drain everything available without blocking. select-loop keeps us nonblocking.
        any_data = False
        while True:
            try:
                ready, _, _ = select.select([self._pty.fd], [], [], 0)
                if not ready:
                    break
                chunk = self._pty.read(4096)
            except EOFError:
                self._teardown()
                break
            except Exception:
                break
            if not chunk:
                break
            self._stream.feed(chunk)
            any_data = True
        if any_data:
            self._refresh()
```

- [ ] **Step 4: Run new tests to verify they pass**

Run: `uv run pytest tests/test_widget_terminal.py -v`
Expected: all terminal tests pass — including the existing ones that call `_tick()` directly (still callable; just becomes a no-op when there's nothing to drain).

- [ ] **Step 5: Run full suite + pyright**

Run: `uv run pytest -x` → all pass. Run: `uv run pyright` → 0 errors.

- [ ] **Step 6: Commit**

```bash
git add patchbai/widgets/terminal.py tests/test_widget_terminal.py
git commit -m "feat(terminal): drive PTY reads via asyncio.add_reader, drop polling"
```

---

## Task 4: Propagate Resize → setwinsize + screen.resize

**Why now:** Renderer correct, event loop correct. Now the visible area should match the Textual panel's actual cell dimensions.

**Files:**
- Modify: `patchbai/widgets/terminal.py` — add `on_resize`. Change DEFAULT_COLS/ROWS handling to use the actual size when available.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_widget_terminal.py`:

```python
@pytest.mark.asyncio
async def test_terminal_resizes_screen_and_pty():
    app = _Host()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        # Give Textual time to deliver the Resize event to the widget.
        await pilot.pause()
        term = app.query_one(Terminal)
        # The Container has 1-cell border + 1-cell padding on each side.
        # We just assert the screen got resized away from 80x24 — exact
        # numbers depend on Textual's layout, so check the trend.
        assert term._screen.columns != 80 or term._screen.lines != 24, (
            f"screen still 80x24 — resize not propagated"
        )
        # And the screen dimensions should be reasonable for a 120x40 host.
        assert term._screen.columns > 24
        assert term._screen.lines > 8
        term._teardown()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_widget_terminal.py::test_terminal_resizes_screen_and_pty -v`
Expected: FAIL — `term._screen.columns == 80 and term._screen.lines == 24` since no resize hookup exists.

- [ ] **Step 3: Edit `patchbai/widgets/terminal.py`**

Add this method to the `Terminal` class (a good spot is right after `on_mount`):

```python
    def on_resize(self, event) -> None:
        """Propagate Textual size changes to the PTY and the pyte screen."""
        if self._pty is None:
            return
        # Use the inner Static's content size — that's where actual cells render.
        try:
            inner = self.query_one("#terminal-screen", Static)
            size = inner.size
        except Exception:
            return
        cols = max(1, size.width)
        rows = max(1, size.height)
        if cols == self._screen.columns and rows == self._screen.lines:
            return
        try:
            self._pty.setwinsize(rows, cols)
        except Exception:
            pass
        try:
            self._screen.resize(rows, cols)
        except Exception:
            pass
        self._refresh()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_widget_terminal.py::test_terminal_resizes_screen_and_pty -v`
Expected: PASS.

If the test still fails because `inner.size` reports `(0, 0)` during the first resize tick: the inner Static may not be laid out yet. In that case, replace `inner = self.query_one(...)` lookup with using `event.size` of the Container itself, minus a small constant for border/padding (the CSS uses `border: round` (1 cell each side) + `padding: 0 1` (1 cell each side) → `cols = event.size.width - 4`, `rows = event.size.height - 2`). Confirm the test still passes after that adjustment.

- [ ] **Step 5: Run full suite + pyright**

Run: `uv run pytest -x` → all pass. Run: `uv run pyright` → 0 errors.

- [ ] **Step 6: Commit**

```bash
git add patchbai/widgets/terminal.py tests/test_widget_terminal.py
git commit -m "feat(terminal): propagate Resize to setwinsize and screen.resize"
```

---

## Task 5: Pure key encoder for arrows, F-keys, Home/End/PgUp/PgDn, Esc, Alt-, Ctrl-letter

**Why now:** Renderer + IO correct; resize correct; now wire input properly. Pure helper first, then integrate.

**Files:**
- Create: `patchbai/widgets/_terminal_keys.py`
- Create: `tests/test_terminal_keys.py`

### Reference — sequences we need to emit

Source: xterm Control Sequences (`ctlseqs.txt`), de-facto standard. Default-on (no DECCKM application mode).

| Key | Bytes |
|---|---|
| Up | `\x1b[A` |
| Down | `\x1b[B` |
| Right | `\x1b[C` |
| Left | `\x1b[D` |
| Home | `\x1b[H` |
| End | `\x1b[F` |
| Page Up | `\x1b[5~` |
| Page Down | `\x1b[6~` |
| Insert | `\x1b[2~` |
| Delete | `\x1b[3~` |
| F1 | `\x1bOP` |
| F2 | `\x1bOQ` |
| F3 | `\x1bOR` |
| F4 | `\x1bOS` |
| F5 | `\x1b[15~` |
| F6 | `\x1b[17~` |
| F7 | `\x1b[18~` |
| F8 | `\x1b[19~` |
| F9 | `\x1b[20~` |
| F10 | `\x1b[21~` |
| F11 | `\x1b[23~` |
| F12 | `\x1b[24~` |
| Esc | `\x1b` |
| Tab | `\t` |
| Shift+Tab | `\x1b[Z` |
| Backspace | `\x7f` |
| Enter | `\r` (was `\n` in current impl — `\r` is what real PTYs deliver; `\n` works on Linux but `\r` is correct) |
| Ctrl+a..Ctrl+z | `chr(ord(letter) - ord('a') + 1)` (1..26) |
| Ctrl+space | `\x00` |
| Ctrl+\\ | `\x1c` |
| Ctrl+] | `\x1d` |
| Ctrl+/ | `\x1f` |
| Alt+x | `\x1b` + x (where x is the un-Alt-ed character/sequence) |

### Textual key event mapping

Textual's `Key` event delivers `event.key` as a string. Some examples we need to handle:
- Arrows: `"up"`, `"down"`, `"left"`, `"right"`
- Navigation: `"home"`, `"end"`, `"pageup"`, `"pagedown"`, `"insert"`, `"delete"`
- F-keys: `"f1"` … `"f12"`
- `"escape"`, `"tab"`, `"shift+tab"`, `"backspace"`, `"enter"`, `"space"`
- Ctrl combos: `"ctrl+a"` … `"ctrl+z"`, `"ctrl+space"`, `"ctrl+backslash"`, `"ctrl+right_square_bracket"`, `"ctrl+slash"`
- Printable: `event.character` is the typed char (single-character str) — pass through.
- Alt-modified: `"alt+x"` etc. — encode as `\x1b` + recursive encoding of `x`.

`event.character` may also contain non-ASCII for IME input — pass through as utf-8.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_terminal_keys.py`:

```python
from patchbai.widgets._terminal_keys import encode_key


def _enc(key: str, character: str | None = None) -> bytes | None:
    return encode_key(key, character)


# --- printable and basic editing ---

def test_printable_letter():
    assert _enc("a", "a") == b"a"


def test_printable_unicode():
    assert _enc("ñ", "ñ") == "ñ".encode("utf-8")


def test_enter_emits_carriage_return():
    assert _enc("enter", None) == b"\r"


def test_tab():
    assert _enc("tab", None) == b"\t"


def test_shift_tab():
    assert _enc("shift+tab", None) == b"\x1b[Z"


def test_backspace():
    assert _enc("backspace", None) == b"\x7f"


def test_escape():
    assert _enc("escape", None) == b"\x1b"


def test_space():
    assert _enc("space", " ") == b" "


# --- arrows ---

def test_arrows():
    assert _enc("up", None) == b"\x1b[A"
    assert _enc("down", None) == b"\x1b[B"
    assert _enc("right", None) == b"\x1b[C"
    assert _enc("left", None) == b"\x1b[D"


# --- navigation ---

def test_home_end():
    assert _enc("home", None) == b"\x1b[H"
    assert _enc("end", None) == b"\x1b[F"


def test_pageup_pagedown():
    assert _enc("pageup", None) == b"\x1b[5~"
    assert _enc("pagedown", None) == b"\x1b[6~"


def test_insert_delete():
    assert _enc("insert", None) == b"\x1b[2~"
    assert _enc("delete", None) == b"\x1b[3~"


# --- F-keys ---

def test_f1_through_f4():
    assert _enc("f1", None) == b"\x1bOP"
    assert _enc("f2", None) == b"\x1bOQ"
    assert _enc("f3", None) == b"\x1bOR"
    assert _enc("f4", None) == b"\x1bOS"


def test_f5_through_f12():
    assert _enc("f5", None) == b"\x1b[15~"
    assert _enc("f6", None) == b"\x1b[17~"
    assert _enc("f7", None) == b"\x1b[18~"
    assert _enc("f8", None) == b"\x1b[19~"
    assert _enc("f9", None) == b"\x1b[20~"
    assert _enc("f10", None) == b"\x1b[21~"
    assert _enc("f11", None) == b"\x1b[23~"
    assert _enc("f12", None) == b"\x1b[24~"


# --- Ctrl combos ---

def test_ctrl_letters_a_through_z():
    for i, ch in enumerate("abcdefghijklmnopqrstuvwxyz"):
        assert _enc(f"ctrl+{ch}", None) == bytes([i + 1]), f"ctrl+{ch}"


def test_ctrl_space_is_null():
    assert _enc("ctrl+space", None) == b"\x00"


def test_ctrl_backslash():
    assert _enc("ctrl+backslash", None) == b"\x1c"


# --- Alt combos ---

def test_alt_letter_prefixes_with_esc():
    assert _enc("alt+a", "a") == b"\x1ba"


def test_alt_arrow():
    assert _enc("alt+up", None) == b"\x1b\x1b[A"


# --- unhandled ---

def test_unknown_key_returns_none():
    assert _enc("super+f", None) is None


def test_no_character_for_printable_key_returns_none():
    # A key string we don't recognize and no character — drop it.
    assert _enc("nonsense_key", None) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_terminal_keys.py -v`
Expected: all fail with `ModuleNotFoundError`.

- [ ] **Step 3: Write the implementation**

Create `patchbai/widgets/_terminal_keys.py`:

```python
"""Pure mapping from Textual key events to xterm-compatible byte sequences.

Default xterm cursor-key mode (no DECCKM application mode) is assumed —
that's what real shells expect by default. If we later support DECCKM,
we'll route through here too.
"""

from __future__ import annotations

ESC = b"\x1b"

_SIMPLE: dict[str, bytes] = {
    "enter": b"\r",
    "tab": b"\t",
    "shift+tab": b"\x1b[Z",
    "backspace": b"\x7f",
    "escape": b"\x1b",
    "up": b"\x1b[A",
    "down": b"\x1b[B",
    "right": b"\x1b[C",
    "left": b"\x1b[D",
    "home": b"\x1b[H",
    "end": b"\x1b[F",
    "pageup": b"\x1b[5~",
    "pagedown": b"\x1b[6~",
    "insert": b"\x1b[2~",
    "delete": b"\x1b[3~",
    "f1": b"\x1bOP",
    "f2": b"\x1bOQ",
    "f3": b"\x1bOR",
    "f4": b"\x1bOS",
    "f5": b"\x1b[15~",
    "f6": b"\x1b[17~",
    "f7": b"\x1b[18~",
    "f8": b"\x1b[19~",
    "f9": b"\x1b[20~",
    "f10": b"\x1b[21~",
    "f11": b"\x1b[23~",
    "f12": b"\x1b[24~",
}

_CTRL_NAMED: dict[str, bytes] = {
    "ctrl+space": b"\x00",
    "ctrl+at": b"\x00",
    "ctrl+backslash": b"\x1c",
    "ctrl+right_square_bracket": b"\x1d",
    "ctrl+slash": b"\x1f",
    "ctrl+underscore": b"\x1f",
    "ctrl+question_mark": b"\x7f",
}


def encode_key(key: str, character: str | None) -> bytes | None:
    """Map a Textual key+character to xterm-style bytes; None if unhandled.

    Args:
        key: Textual's key descriptor (e.g. "up", "ctrl+c", "alt+x", "f5").
        character: The typed character if any (Textual provides this for
            printable keys including Unicode).
    """
    # Alt+X → ESC + (recursively encoded X).
    if key.startswith("alt+"):
        rest = key[len("alt+") :]
        sub = encode_key(rest, character if rest == character else None)
        if sub is None and character is not None:
            sub = character.encode("utf-8")
        return None if sub is None else ESC + sub

    if key in _SIMPLE:
        return _SIMPLE[key]

    if key.startswith("ctrl+"):
        suffix = key[len("ctrl+") :]
        if len(suffix) == 1 and suffix.isalpha():
            return bytes([ord(suffix.lower()) - ord("a") + 1])
        if key in _CTRL_NAMED:
            return _CTRL_NAMED[key]
        return None

    if key == "space" and character == " ":
        return b" "

    if character is not None and len(character) >= 1:
        return character.encode("utf-8")

    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_terminal_keys.py -v`
Expected: all PASS.

- [ ] **Step 5: Verify pyright clean**

Run: `uv run pyright patchbai/widgets/_terminal_keys.py tests/test_terminal_keys.py`
Expected: 0 errors.

- [ ] **Step 6: Commit**

```bash
git add patchbai/widgets/_terminal_keys.py tests/test_terminal_keys.py
git commit -m "feat(terminal): pure key encoder for xterm-style sequences"
```

---

## Task 6: Wire `encode_key` into the widget's `on_key`

**Why now:** Helper proven; integrate.

**Files:**
- Modify: `patchbai/widgets/terminal.py` — replace `on_key`. Also add an `import` for the helper.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_widget_terminal.py`:

```python
@pytest.mark.asyncio
async def test_terminal_forwards_arrow_key_bytes(tmp_path):
    """Pressing arrow keys writes xterm sequences to the PTY."""
    app = _Host(command=["/bin/cat"])  # cat echoes its stdin to stdout
    async with app.run_test() as pilot:
        await pilot.pause()
        term = app.query_one(Terminal)
        await pilot.press("up")
        await pilot.pause()
        # cat echoes back; the screen should now contain the ESC[A bytes
        # rendered (pyte will swallow ESC[A as a no-op cursor command, BUT
        # the PTY write itself is the test). Inspect via a stub spy: easiest
        # is to assert _pty.write was called with the right bytes via the
        # _last_write attribute we'll add.
        assert getattr(term, "_last_write", None) == b"\x1b[A"
        term._teardown()


@pytest.mark.asyncio
async def test_terminal_forwards_ctrl_letter():
    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        term = app.query_one(Terminal)
        await pilot.press("ctrl+l")
        await pilot.pause()
        assert getattr(term, "_last_write", None) == b"\x0c"
        term._teardown()


@pytest.mark.asyncio
async def test_terminal_drops_unknown_key_silently():
    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        term = app.query_one(Terminal)
        # super+x is not something we handle
        await pilot.press("super+x")
        await pilot.pause()
        assert getattr(term, "_last_write", None) is None
        term._teardown()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_widget_terminal.py::test_terminal_forwards_arrow_key_bytes tests/test_widget_terminal.py::test_terminal_forwards_ctrl_letter tests/test_widget_terminal.py::test_terminal_drops_unknown_key_silently -v`
Expected: all fail — current `on_key` doesn't handle arrows or ctrl+l, and there's no `_last_write` field.

- [ ] **Step 3: Edit `patchbai/widgets/terminal.py`**

(a) Add an import next to the renderer import:

```python
from patchbai.widgets._terminal_keys import encode_key
```

(b) Initialize the spy field in `__init__`:

```python
        self._last_write: bytes | None = None
```

(c) Replace the entire `on_key` method (lines 135–160 in the original) with:

```python
    def on_key(self, event) -> None:
        if self._pty is None:
            return
        data = encode_key(event.key, event.character)
        if data is None:
            return
        try:
            # PtyProcessUnicode.write expects str; round-trip safely.
            self._pty.write(data.decode("utf-8", errors="replace"))
        except Exception:
            return
        self._last_write = data
        event.stop()
```

(`_last_write` is intentionally *informational* / spy — it lets tests inspect what the widget wrote without intercepting the PTY. It is private and not part of any public API.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_widget_terminal.py -v`
Expected: all terminal tests pass.

- [ ] **Step 5: Run full suite + pyright**

Run: `uv run pytest -x` → all pass. Run: `uv run pyright` → 0 errors.

- [ ] **Step 6: Commit**

```bash
git add patchbai/widgets/terminal.py tests/test_widget_terminal.py
git commit -m "feat(terminal): forward arrows/F-keys/Ctrl/Alt via encode_key"
```

---

## Task 7: Surface process exit and add a Restart action

**Why now:** Last user-visible bug from the report — when the shell exits, the panel just freezes silently.

**Files:**
- Modify: `patchbai/widgets/terminal.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_widget_terminal.py`:

```python
@pytest.mark.asyncio
async def test_terminal_announces_exit():
    import asyncio
    # `true` exits with status 0 immediately.
    app = _Host(command=["/usr/bin/true"])
    async with app.run_test() as pilot:
        await pilot.pause()
        term = app.query_one(Terminal)
        # Wait for the child to exit and add_reader to fire.
        for _ in range(50):
            await asyncio.sleep(0.02)
            await pilot.pause()
            if term._pty is None:
                break
        text = "\n".join(term._screen.display)
        assert "[process exited" in text, f"exit banner missing; got:\n{text!r}"
        term._teardown()


@pytest.mark.asyncio
async def test_terminal_restart_respawns():
    app = _Host(command=["/usr/bin/true"])
    async with app.run_test() as pilot:
        import asyncio
        await pilot.pause()
        term = app.query_one(Terminal)
        for _ in range(50):
            await asyncio.sleep(0.02)
            await pilot.pause()
            if term._pty is None:
                break
        # Now restart.
        term.action_restart()
        await pilot.pause()
        assert term._pty is not None
        term._teardown()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_widget_terminal.py::test_terminal_announces_exit tests/test_widget_terminal.py::test_terminal_restart_respawns -v`
Expected: both fail.

- [ ] **Step 3: Edit `patchbai/widgets/terminal.py`**

(a) Update `_tick` to detect EOF and call a new `_announce_exit` helper instead of teardown-only:

```python
    def _tick(self) -> None:
        if self._pty is None:
            return
        any_data = False
        eof = False
        while True:
            try:
                ready, _, _ = select.select([self._pty.fd], [], [], 0)
                if not ready:
                    break
                chunk = self._pty.read(4096)
            except EOFError:
                eof = True
                break
            except Exception:
                break
            if not chunk:
                eof = True
                break
            self._stream.feed(chunk)
            any_data = True
        if eof:
            self._announce_exit()
        elif any_data:
            self._refresh()
```

(b) Add the `_announce_exit` method:

```python
    def _announce_exit(self) -> None:
        # Drain status if available, then tear down the reader/pty.
        status = None
        if self._pty is not None:
            try:
                status = self._pty.exitstatus
            except Exception:
                status = None
        # Feed a banner into the pyte stream so it shows up under the last line
        # with the renderer we already have.
        banner = f"\r\n[process exited {status if status is not None else '?'}]\r\n"
        try:
            self._stream.feed(banner)
        except Exception:
            pass
        self._teardown()
        self._refresh()
```

(c) Add a public `action_restart` method (Textual binds these as actions, but it's also fine to call directly):

```python
    def action_restart(self) -> None:
        """Respawn the subprocess in-place. Safe to call after exit."""
        if self._pty is not None:
            # Already running — nothing to do.
            return
        # Reset the screen so the old session's tail doesn't accumulate forever.
        self._screen = pyte.HistoryScreen(
            self._screen.columns, self._screen.lines, history=2000, ratio=0.5
        )
        self._stream = pyte.Stream(self._screen)
        self.on_mount()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_widget_terminal.py -v`
Expected: all terminal tests pass.

- [ ] **Step 5: Run full suite + pyright**

Run: `uv run pytest -x` → all pass. Run: `uv run pyright` → 0 errors.

- [ ] **Step 6: Commit**

```bash
git add patchbai/widgets/terminal.py tests/test_widget_terminal.py
git commit -m "feat(terminal): announce exit status and add action_restart"
```

---

## Task 8: Wrap-up — final verification, before/after summary, no commit unless something is found

- [ ] **Step 1: Run the entire suite verbose**

Run: `uv run pytest -v`
Expected: all green; capture the count.

- [ ] **Step 2: Run pyright across the project**

Run: `uv run pyright`
Expected: `0 errors, 0 warnings, 0 informations`.

- [ ] **Step 3: Smoke-run the terminal widget by hand (optional)**

Run: `uv run patchbai` (or `uv run mt`) and exercise the terminal panel:
- Type a printable string. Confirm cursor advances visibly.
- Press arrows at a shell prompt — history should scroll.
- Run `ls --color=auto` — file colors should appear.
- Resize the terminal window — running `top` or `htop` should redraw at the new size.
- Run `exit` — the `[process exited 0]` banner should appear.

This is a manual sanity check; do not block on it if not feasible in the dev environment.

- [ ] **Step 4: Diff summary of `patchbai/widgets/terminal.py` against `terminal-research`'s starting point**

Run: `git log --oneline 52f8c77..HEAD -- patchbai/widgets/terminal.py patchbai/widgets/_terminal_render.py patchbai/widgets/_terminal_keys.py`
Expected: 6 substantive commits (Tasks 1–7 minus Task 1 which only adds the helper module).

- [ ] **Step 5: Report back to the orchestrator**

Final report to the orchestrator should include:
- Final commit hash on `terminal-research`.
- Test counts: e.g., before = N; after = M.
- 1-paragraph summary listing which renderer issues actually got fixed (cite the §1 list from the research report).

No commit at this step unless the verification flushed out something.

---

## Self-review notes (already addressed inline)

- **Spec coverage:** §1 of the report listed 9 categories of gaps. Tasks 1–2 cover §1.1 (rendering / colors), §1.3 (encode round-trip), §1.7 (alt-screen rendering bug — implicit because we walk buffer not display). Task 2 also covers §1.2 (HistoryScreen / scrollback). Task 3 covers §1.4 (polling). Task 4 covers §1.2 (resize). Tasks 5–6 cover §1.5 (keyboard). Task 7 covers §1.8 (lifecycle). §1.6 (mouse) is intentionally deferred — out of scope. §1.7 OSC titles, OSC 8, bracketed paste are deferred to Phase 2 (engine swap), as discussed in the report.
- **No placeholders:** every task has full code; no "implement appropriate handler" handwaving.
- **Type consistency:** `encode_key(key, character) -> bytes | None` is used identically in helper and widget. Renderer signature `render_screen(screen, *, show_cursor: bool) -> Text` is used identically.
- **Optional Task 4 fallback:** the resize task lists a fallback (`event.size` minus border/padding) to be used if the inner `Static.size` reports 0 — but the failing test still has to pass.
- **Test isolation:** every TDD cycle calls `term._teardown()` to clean up the PTY before the test exits. Without this, lingering child processes can confuse the test runner across the suite.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-07-terminal-renderer-phase1.md`. Two execution options:

1. **Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** — execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
