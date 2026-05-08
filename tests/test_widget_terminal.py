import os

import pytest
from textual.app import App

from patchfeld.widgets.terminal import Terminal


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


@pytest.mark.asyncio
async def test_terminal_resizes_screen_and_pty():
    app = _Host()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        # Give Textual time to deliver the Resize event to the widget.
        await pilot.pause()
        term = app.query_one(Terminal)
        # 120x40 host minus CSS chrome (border 1 each side + horizontal
        # padding 1 each side -> -4 width, -2 height) = 116x38.
        assert term._screen.columns == 116
        assert term._screen.lines == 38
        # The PTY's window size must match — proves both the screen.resize
        # AND setwinsize legs of the propagation actually fired.
        assert term._pty is not None
        pty_rows, pty_cols = term._pty.getwinsize()
        assert pty_cols == term._screen.columns
        assert pty_rows == term._screen.lines
        term._teardown()


@pytest.mark.asyncio
async def test_terminal_forwards_arrow_key_bytes():
    """Pressing arrow keys writes xterm sequences to the PTY."""
    app = _Host(command=["/bin/cat"])  # cat echoes its stdin to stdout
    async with app.run_test() as pilot:
        await pilot.pause()
        term = app.query_one(Terminal)
        await pilot.press("up")
        await pilot.pause()
        # The widget records the bytes it forwarded in _last_write.
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


@pytest.mark.asyncio
async def test_terminal_forwards_printable_letter():
    """Most common keystroke — typing a letter — must reach the PTY as-is."""
    app = _Host(command=["/bin/cat"])
    async with app.run_test() as pilot:
        await pilot.pause()
        term = app.query_one(Terminal)
        await pilot.press("a")
        await pilot.pause()
        assert term._last_write == b"a"
        term._teardown()


@pytest.mark.asyncio
async def test_terminal_forwards_backspace():
    """Backspace was handled by the old whitelist; verify the new encoder path keeps it."""
    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        term = app.query_one(Terminal)
        await pilot.press("backspace")
        await pilot.pause()
        assert term._last_write == b"\x7f"
        term._teardown()


@pytest.mark.asyncio
async def test_terminal_forwards_alt_arrow():
    """Alt+ recursion through encode_key must route correctly via on_key."""
    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        term = app.query_one(Terminal)
        await pilot.press("alt+up")
        await pilot.pause()
        assert term._last_write == b"\x1b\x1b[A"
        term._teardown()


@pytest.mark.asyncio
async def test_terminal_forwards_function_key():
    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        term = app.query_one(Terminal)
        await pilot.press("f5")
        await pilot.pause()
        assert term._last_write == b"\x1b[15~"
        term._teardown()


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
    import asyncio
    app = _Host(command=["/usr/bin/true"])
    async with app.run_test() as pilot:
        await pilot.pause()
        term = app.query_one(Terminal)
        for _ in range(50):
            await asyncio.sleep(0.02)
            await pilot.pause()
            if term._pty is None:
                break
        # Now restart. Capture the new PTY reference synchronously, since
        # `/usr/bin/true` may exit again before pilot.pause() returns and the
        # add_reader callback may already have torn it down.
        term.action_restart()
        respawned = term._pty
        await pilot.pause()
        assert respawned is not None
        term._teardown()


@pytest.mark.asyncio
async def test_terminal_announces_nonzero_exit_status():
    import asyncio
    app = _Host(command=["/bin/sh", "-c", "exit 7"])
    async with app.run_test() as pilot:
        await pilot.pause()
        term = app.query_one(Terminal)
        for _ in range(50):
            await asyncio.sleep(0.02)
            await pilot.pause()
            if term._pty is None:
                break
        text = "\n".join(term._screen.display)
        assert "[process exited 7]" in text, f"expected exit-7 banner, got:\n{text!r}"
        term._teardown()


@pytest.mark.asyncio
async def test_terminal_restart_is_noop_while_alive():
    """Calling action_restart while the child is alive must not double-spawn."""
    app = _Host(command=["/bin/cat"])  # cat sticks around indefinitely
    async with app.run_test() as pilot:
        await pilot.pause()
        term = app.query_one(Terminal)
        original_pty = term._pty
        assert original_pty is not None
        term.action_restart()  # should be a no-op since the child is alive
        assert term._pty is original_pty
        term._teardown()


def test_terminal_can_focus():
    """Container.can_focus defaults to False; without override on_key would be dead code."""
    assert Terminal.can_focus is True


@pytest.mark.asyncio
async def test_terminal_drain_loop_respects_read_budget():
    """When more bytes are buffered than READ_BUDGET_BYTES, _tick stops and lets add_reader re-fire."""
    import asyncio
    # Print ~80 KiB so a single _tick definitely runs into the 64 KiB cap.
    payload_cmd = "i=0; while [ $i -lt 5000 ]; do echo line-$i-padding-padding-padding; i=$((i+1)); done"
    app = _Host(command=["/bin/sh", "-c", payload_cmd])
    async with app.run_test() as pilot:
        await pilot.pause()
        term = app.query_one(Terminal)
        # Drive _tick once manually so we can observe the bytes_read budget directly.
        # We can't easily inspect bytes_read from outside, so instead we verify two
        # invariants: (1) eventually all output lands; (2) the drain doesn't crash.
        for _ in range(80):
            await asyncio.sleep(0.02)
            await pilot.pause()
        text = "\n".join(term._screen.display)
        assert "line-4999" in text, f"latest line missing; got tail: {text[-200:]!r}"
        # Scrollback should be substantial — proves we didn't drop bytes at the cap boundary.
        assert len(term._screen.history.top) > 100
        term._teardown()
