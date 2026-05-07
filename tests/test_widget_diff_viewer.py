import pytest
from textual.app import App

from patchbai.widgets.diff_viewer import DiffViewer


class _Host(App):
    def __init__(self, **kwargs):
        super().__init__()
        self._kwargs = kwargs

    def compose(self):
        yield DiffViewer(**self._kwargs)


@pytest.mark.asyncio
async def test_diff_viewer_renders_precomputed_diff():
    diff = "--- a\n+++ b\n@@ -1 +1 @@\n-old\n+new\n"
    app = _Host(diff=diff)
    async with app.run_test() as pilot:
        await pilot.pause()
        viewer = app.query_one(DiffViewer)
        assert "+new" in viewer.diff_text
        assert "-old" in viewer.diff_text


@pytest.mark.asyncio
async def test_diff_viewer_computes_from_before_after():
    app = _Host(before="line 1\nline 2\n", after="line 1\nline 2 changed\n")
    async with app.run_test() as pilot:
        await pilot.pause()
        viewer = app.query_one(DiffViewer)
        assert "-line 2" in viewer.diff_text
        assert "+line 2 changed" in viewer.diff_text


@pytest.mark.asyncio
async def test_diff_viewer_no_inputs_renders_empty_message():
    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        viewer = app.query_one(DiffViewer)
        # diff_text is the source-of-truth attribute; it's empty when no
        # inputs were provided. The placeholder ("No diff to display") is
        # rendered as a Static child but not stored back into diff_text.
        assert viewer.diff_text == ""
