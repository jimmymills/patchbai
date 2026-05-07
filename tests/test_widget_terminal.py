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
        # At least one span must carry red coloring across cells [0..3)
        red_spans = [
            s for s in rendered.spans
            if s.start < 3
            and getattr(getattr(s.style, "color", None), "name", None) == "red"
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
