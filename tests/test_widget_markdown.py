from pathlib import Path

import pytest
from textual.app import App

from patchfeld.widgets.markdown import Markdown


class _Host(App):
    def __init__(self, **kwargs):
        super().__init__()
        self._kwargs = kwargs

    def compose(self):
        yield Markdown(**self._kwargs)


@pytest.mark.asyncio
async def test_markdown_renders_inline_source():
    app = _Host(source="# Hello\n\nWorld")
    async with app.run_test() as pilot:
        await pilot.pause()
        widget = app.query_one(Markdown)
        # Just verify the widget mounted with our source content visible.
        assert "Hello" in widget._markdown


@pytest.mark.asyncio
async def test_markdown_loads_from_file_path(tmp_path: Path):
    md_path = tmp_path / "doc.md"
    md_path.write_text("# From file\n", encoding="utf-8")
    app = _Host(file_path=str(md_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        widget = app.query_one(Markdown)
        assert "From file" in widget._markdown


@pytest.mark.asyncio
async def test_markdown_missing_file_renders_error_text(tmp_path: Path):
    app = _Host(file_path=str(tmp_path / "nope.md"))
    async with app.run_test() as pilot:
        await pilot.pause()
        widget = app.query_one(Markdown)
        assert "not found" in widget._markdown.lower() or "missing" in widget._markdown.lower()
