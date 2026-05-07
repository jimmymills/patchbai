from pathlib import Path

import pytest
from textual.app import App
from textual.widgets import DataTable

from mod_tui.agents.state import AgentInfo, AgentState
from mod_tui.events import AgentSpawned, AgentStateChanged, EventBus
from mod_tui.persistence.agents_index import AgentsIndex
from mod_tui.widgets.agent_table import AgentTable


class _HostApp(App):
    def __init__(self, bus: EventBus, cwd: Path | None = None) -> None:
        super().__init__()
        self.event_bus = bus
        if cwd is not None:
            self.cwd = cwd  # type: ignore[assignment]

    def compose(self):
        yield AgentTable(event_bus=self.event_bus)


def _info(id: str = "a1", state: AgentState = AgentState.RUNNING) -> AgentInfo:
    return AgentInfo(id=id, name=f"agent-{id}", cwd="/tmp", started_at=100.0, state=state)


@pytest.mark.asyncio
async def test_agent_spawned_adds_row():
    bus = EventBus()
    app = _HostApp(bus)
    async with app.run_test() as pilot:
        await pilot.pause()
        bus.publish(AgentSpawned(info=_info()))
        await pilot.pause()

        table = app.query_one(AgentTable).query_one(DataTable)
        assert table.row_count == 1


@pytest.mark.asyncio
async def test_agent_state_changed_updates_row():
    bus = EventBus()
    app = _HostApp(bus)
    async with app.run_test() as pilot:
        await pilot.pause()
        info = _info()
        bus.publish(AgentSpawned(info=info))
        info.state = AgentState.DONE
        bus.publish(AgentStateChanged(info=info, old_state=AgentState.RUNNING))
        await pilot.pause()

        table = app.query_one(AgentTable).query_one(DataTable)
        assert table.row_count == 1


@pytest.mark.asyncio
async def test_agent_table_seeds_past_agents_from_disk(tmp_path: Path):
    # Regression: after a crash/restart, the AgentTable used to come up empty
    # and the user lost sight of agents they'd been working with. The widget
    # now seeds rows from agents.json on mount.
    idx = AgentsIndex(cwd=tmp_path)
    idx.save([
        _info("done-1", state=AgentState.DONE),
        _info("err-1", state=AgentState.ERROR),
        # The orchestrator entry must NOT show up in this table.
        AgentInfo(id="orchestrator", name="orchestrator", cwd="/tmp",
                  started_at=100.0, state=AgentState.DONE),
    ])

    bus = EventBus()
    app = _HostApp(bus, cwd=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one(AgentTable).query_one(DataTable)
        assert table.row_count == 2
        keys = {str(row.value) for row in table.rows.keys()}
        assert keys == {"done-1", "err-1"}


@pytest.mark.asyncio
async def test_agent_table_seed_does_not_double_count_live_spawn(tmp_path: Path):
    # Live AgentSpawned for an id already on disk must update, not duplicate.
    idx = AgentsIndex(cwd=tmp_path)
    idx.save([_info("a1", state=AgentState.ERROR)])

    bus = EventBus()
    app = _HostApp(bus, cwd=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Same id "a1" — the seed already added it; the spawn is a no-op.
        bus.publish(AgentSpawned(info=_info("a1", state=AgentState.RUNNING)))
        await pilot.pause()
        table = app.query_one(AgentTable).query_one(DataTable)
        assert table.row_count == 1
