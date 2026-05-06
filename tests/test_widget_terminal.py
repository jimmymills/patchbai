import os

import pytest
from textual.app import App

from mod_tui.widgets.terminal import Terminal


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
