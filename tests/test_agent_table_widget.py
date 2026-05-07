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


@pytest.mark.asyncio
async def test_status_cell_uses_yellow_for_waiting():
    from rich.text import Text
    bus = EventBus()
    app = _HostApp(bus)
    async with app.run_test() as pilot:
        await pilot.pause()
        info = _info(state=AgentState.WAITING)
        bus.publish(AgentSpawned(info=info))
        await pilot.pause()

        widget = app.query_one(AgentTable)
        cells = widget._render_cells(info)
        # Status is column index 1.
        status_cell = cells[1]
        assert isinstance(status_cell, Text)
        assert status_cell.plain == "waiting"
        assert "yellow" in str(status_cell.style).lower()


@pytest.mark.asyncio
async def test_status_cell_uses_green_for_running():
    from rich.text import Text
    bus = EventBus()
    app = _HostApp(bus)
    async with app.run_test() as pilot:
        await pilot.pause()
        info = _info(state=AgentState.RUNNING)
        widget = app.query_one(AgentTable)
        cells = widget._render_cells(info)
        status_cell = cells[1]
        assert isinstance(status_cell, Text)
        assert status_cell.plain == "running"
        assert "green" in str(status_cell.style).lower()


@pytest.mark.asyncio
async def test_status_cell_uses_red_for_error():
    from rich.text import Text
    bus = EventBus()
    app = _HostApp(bus)
    async with app.run_test() as pilot:
        await pilot.pause()
        info = _info(state=AgentState.ERROR)
        widget = app.query_one(AgentTable)
        cells = widget._render_cells(info)
        status_cell = cells[1]
        assert isinstance(status_cell, Text)
        assert status_cell.plain == "error"
        assert "red" in str(status_cell.style).lower()
