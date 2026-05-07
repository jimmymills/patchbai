import pytest
from textual.app import App
from textual.widgets import DataTable

from patchbai.agents.state import AgentInfo, AgentState
from patchbai.persistence.agents_index import AgentsIndex
from patchbai.widgets.history_screen import HistoryScreen


@pytest.mark.asyncio
async def test_history_lists_agents_from_index(tmp_path):
    idx = AgentsIndex(cwd=tmp_path)
    idx.upsert(AgentInfo(id="a1", name="alpha", cwd="/tmp", started_at=100.0,
                         state=AgentState.DONE, ended_at=200.0))
    idx.upsert(AgentInfo(id="a2", name="beta", cwd="/tmp", started_at=300.0,
                         state=AgentState.DONE, ended_at=400.0))

    selected: list[str | None] = []

    class _Host(App):
        async def on_mount(self):
            screen = HistoryScreen(index=idx)
            await self.push_screen(screen, selected.append)

    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        table = screen.query_one(DataTable)
        assert table.row_count == 2
        screen.dismiss("a1")
        await pilot.pause()

    assert selected == ["a1"]


@pytest.mark.asyncio
async def test_history_dismisses_with_none_on_escape(tmp_path):
    idx = AgentsIndex(cwd=tmp_path)
    idx.upsert(AgentInfo(id="a1", name="alpha", cwd="/tmp", started_at=100.0))

    selected: list[str | None] = []

    class _Host(App):
        async def on_mount(self):
            await self.push_screen(HistoryScreen(index=idx), selected.append)

    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()

    assert selected == [None]
