import os

import pytest
from textual.app import App

from patchbai.widgets.terminal import Terminal


class _Host(App):
    def __init__(self, **kwargs):
        super().__init__()
        self._kwargs = kwargs

    def compose(self):
        yield Terminal(**self._kwargs)


@pytest.mark.asyncio
async def test_terminal_mounts_with_default_shell():
    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        term = app.query_one(Terminal)
        assert term._pty is not None
        term._teardown()


@pytest.mark.asyncio
async def test_terminal_mounts_with_custom_command(tmp_path):
    app = _Host(command=["/bin/cat"], cwd=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        term = app.query_one(Terminal)
        assert term._pty is not None
        term._teardown()


@pytest.mark.asyncio
async def test_terminal_renders_subprocess_output(tmp_path):
    app = _Host(command=["/bin/sh", "-c", "echo hello-from-pty"])
    async with app.run_test() as pilot:
        await pilot.pause()
        term = app.query_one(Terminal)
        for _ in range(20):
            term._tick()
            await pilot.pause()
        text = "\n".join(term._screen.display)
        assert "hello-from-pty" in text
        term._teardown()


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
        from textual.widgets import Static
        screen_widget = term.query_one("#terminal-screen", Static)
        rendered = screen_widget.content
        assert isinstance(rendered, Text)
        from rich.style import Style
        red_spans = [
            s for s in rendered.spans
            if isinstance(s.style, Style)
            and rendered.plain[s.start:s.end] == "RED"
            and s.style.color is not None
            and s.style.color.name == "red"
        ]
        assert red_spans, f"expected a red span over 'RED', got spans={rendered.spans!r}"
        term._teardown()


@pytest.mark.asyncio
async def test_terminal_scrollback_accumulates_past_visible_window():
    """HistoryScreen must collect lines that scroll off the top of the visible 24-row window."""
    # Print 40 lines so ~16 scroll out of the 24-row default window.
    cmd = ["/bin/sh", "-c", "i=1; while [ $i -le 40 ]; do echo line-$i; i=$((i+1)); done"]
    app = _Host(command=cmd)
    async with app.run_test() as pilot:
        await pilot.pause()
        term = app.query_one(Terminal)
        for _ in range(40):
            term._tick()
            await pilot.pause()
        # The earliest lines should now live in screen.history.top, not screen.display.
        # pyte.HistoryScreen exposes .history with .top (deque of off-screen rows).
        assert len(term._screen.history.top) > 0, (
            f"expected scrollback rows in history.top; "
            f"history.top size={len(term._screen.history.top)}"
        )
        term._teardown()


@pytest.mark.asyncio
async def test_terminal_handles_non_ascii_output():
    # Print é (U+00E9, two bytes in UTF-8) and an emoji (4 bytes).
    app = _Host(command=["/bin/sh", "-c", "printf 'caf\\xc3\\xa9 \\xf0\\x9f\\x9a\\x80'"])
    async with app.run_test() as pilot:
        await pilot.pause()
        term = app.query_one(Terminal)
        for _ in range(20):
            term._tick()
            await pilot.pause()
        text = "\n".join(term._screen.display)
        assert "café" in text, f"expected 'café' in screen, got {text!r}"
        assert "🚀" in text, f"expected rocket emoji in screen, got {text!r}"
        term._teardown()


@pytest.mark.asyncio
async def test_terminal_uses_add_reader_not_timer():
    """on_mount should register an fd reader on the asyncio loop, not a timer."""
    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        term = app.query_one(Terminal)
        # Internal flag set when add_reader was used:
        assert term._pty is not None
        assert getattr(term, "_reader_registered", False) is True
        term._teardown()
        # After teardown the reader is gone.
        assert getattr(term, "_reader_registered", False) is False


@pytest.mark.asyncio
async def test_terminal_drains_via_add_reader():
    """Output appears without anyone calling _tick manually."""
    import asyncio
    app = _Host(command=["/bin/sh", "-c", "printf hello-async; sleep 0.3"])
    async with app.run_test() as pilot:
        await pilot.pause()
        term = app.query_one(Terminal)
        assert term._timer is None, "expected timer-based polling to be gone after Task 3"
        # Give the loop time to drain stdout via add_reader, no _tick calls.
        for _ in range(20):
            await asyncio.sleep(0.02)
            await pilot.pause()
        text = "\n".join(term._screen.display)
        assert "hello-async" in text
        term._teardown()


@pytest.mark.asyncio
async def test_terminal_drains_large_burst_within_one_tick():
    """A multi-iteration drain loop must collect everything that's already buffered."""
    import asyncio
    # ~12 KiB of distinct lines so the drain loop iterates more than once on each
    # add_reader fire (default read size is 4096 bytes per iteration).
    payload_cmd = "i=0; while [ $i -lt 800 ]; do echo line-$i; i=$((i+1)); done"
    app = _Host(command=["/bin/sh", "-c", payload_cmd])
    async with app.run_test() as pilot:
        await pilot.pause()
        term = app.query_one(Terminal)
        # Give the loop generous time to drain via add_reader (no manual _tick).
        for _ in range(50):
            await asyncio.sleep(0.02)
            await pilot.pause()
        # The latest line in the visible window should be at or near line-799,
        # and earlier lines should have made it into scrollback.
        text = "\n".join(term._screen.display)
        assert "line-799" in text, f"missing tail of large burst; got {text[-200:]!r}"
        assert len(term._screen.history.top) > 0, "expected scrollback rows"
        term._teardown()
