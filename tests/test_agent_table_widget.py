import pytest
from textual.app import App
from textual.widgets import DataTable

from mod_tui.agents.state import AgentInfo, AgentState
from mod_tui.events import AgentSpawned, AgentStateChanged, EventBus
from mod_tui.widgets.agent_table import AgentTable


class _HostApp(App):
    def __init__(self, bus: EventBus) -> None:
        super().__init__()
        self.event_bus = bus

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
