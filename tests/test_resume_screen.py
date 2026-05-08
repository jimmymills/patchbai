import pytest
from textual.app import App

from patchfeld.persistence.orchestrator_sessions import (
    OrchestratorSessionEntry,
    OrchestratorSessionsIndex,
)
from patchfeld.widgets.resume_screen import ResumeScreen


def _seed(tmp_path):
    idx = OrchestratorSessionsIndex(cwd=tmp_path)
    idx.upsert(OrchestratorSessionEntry(
        session_id="aaa", transcript_path="x.jsonl",
        started_at=100.0, last_activity=300.0,
        first_user_message="newest one", num_turns=5,
        tokens_in=10, tokens_out=20, cost=0.01,
    ))
    idx.upsert(OrchestratorSessionEntry(
        session_id="bbb", transcript_path="y.jsonl",
        started_at=50.0, last_activity=200.0,
        first_user_message="middle", num_turns=3,
    ))
    idx.upsert(OrchestratorSessionEntry(
        session_id="legacy-1", transcript_path="z.jsonl",
        started_at=10.0, last_activity=100.0,
        legacy=True,
    ))
    return idx


class _Host(App):
    def __init__(self, idx):
        super().__init__()
        self._idx = idx
        self.picked = "unset"

    def on_mount(self):
        def _on_picked(value):
            self.picked = value
        self.push_screen(ResumeScreen(index=self._idx), _on_picked)


@pytest.mark.asyncio
async def test_resume_screen_lists_entries_sorted_by_recency(tmp_path):
    idx = _seed(tmp_path)
    app = _Host(idx)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, ResumeScreen)
        # Expect rows in order: aaa, bbb, legacy-1 (by last_activity desc).
        # _row_session_ids exposes the displayed order for tests.
        assert screen._row_session_ids() == ["aaa", "bbb", "legacy-1"]


@pytest.mark.asyncio
async def test_resume_screen_escape_returns_none(tmp_path):
    idx = _seed(tmp_path)
    app = _Host(idx)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert app.picked is None


@pytest.mark.asyncio
async def test_resume_screen_enter_returns_session_id(tmp_path):
    idx = _seed(tmp_path)
    app = _Host(idx)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Cursor starts on row 0 ("aaa").
        await pilot.press("enter")
        await pilot.pause()
        assert app.picked == "aaa"
