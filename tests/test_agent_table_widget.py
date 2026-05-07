import dataclasses
from pathlib import Path

import pytest
from textual.app import App
from textual.widgets import DataTable

from mod_tui.agents.state import AgentInfo, AgentState
from mod_tui.events import (
    AgentArchiveChanged,
    AgentSpawned,
    AgentStateChanged,
    EventBus,
)
from mod_tui.persistence.agents_index import AgentsIndex
from mod_tui.widgets.agent_table import AgentTable


class _StubManager:
    """Records set_archived calls and (optionally) re-publishes the matching
    AgentArchiveChanged event so the AgentTable refreshes — same contract as
    the real AgentManager."""

    def __init__(self, bus: EventBus, infos: dict[str, AgentInfo]) -> None:
        self._bus = bus
        self._infos = infos
        self.calls: list[tuple[str, bool]] = []

    def set_archived(self, agent_id: str, *, archived: bool) -> None:
        self.calls.append((agent_id, archived))
        info = self._infos.get(agent_id)
        if info is None:
            return
        info.archived = archived
        self._bus.publish(AgentArchiveChanged(info=dataclasses.replace(info)))


class _HostApp(App):
    def __init__(
        self,
        bus: EventBus,
        manager: _StubManager | None = None,
        cwd: Path | None = None,
    ) -> None:
        super().__init__()
        self.event_bus = bus
        # AgentTable looks up `app.manager` for the archive action.
        self.manager = manager
        if cwd is not None:
            self.cwd = cwd  # type: ignore[assignment]

    def compose(self):
        yield AgentTable(event_bus=self.event_bus)


def _info(
    id: str = "a1",
    state: AgentState = AgentState.RUNNING,
    archived: bool = False,
) -> AgentInfo:
    return AgentInfo(
        id=id, name=f"agent-{id}", cwd="/tmp", started_at=100.0,
        state=state, archived=archived,
    )


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
async def test_archived_agent_hidden_by_default():
    bus = EventBus()
    app = _HostApp(bus)
    async with app.run_test() as pilot:
        await pilot.pause()
        bus.publish(AgentSpawned(info=_info("visible")))
        bus.publish(AgentSpawned(info=_info("hidden", archived=True)))
        await pilot.pause()

        table = app.query_one(AgentTable).query_one(DataTable)
        assert table.row_count == 1


@pytest.mark.asyncio
async def test_archive_event_removes_row_from_table():
    bus = EventBus()
    app = _HostApp(bus)
    async with app.run_test() as pilot:
        await pilot.pause()
        info = _info()
        bus.publish(AgentSpawned(info=info))
        await pilot.pause()
        table = app.query_one(AgentTable).query_one(DataTable)
        assert table.row_count == 1

        archived = dataclasses.replace(info, archived=True)
        bus.publish(AgentArchiveChanged(info=archived))
        await pilot.pause()
        assert table.row_count == 0


@pytest.mark.asyncio
async def test_pressing_a_toggles_archived_visibility():
    bus = EventBus()
    app = _HostApp(bus)
    async with app.run_test() as pilot:
        await pilot.pause()
        bus.publish(AgentSpawned(info=_info("visible")))
        bus.publish(AgentSpawned(info=_info("hidden", archived=True)))
        await pilot.pause()

        widget = app.query_one(AgentTable)
        table = widget.query_one(DataTable)
        table.focus()
        await pilot.pause()
        assert table.row_count == 1

        await pilot.press("a")
        await pilot.pause()
        assert table.row_count == 2

        await pilot.press("a")
        await pilot.pause()
        assert table.row_count == 1


@pytest.mark.asyncio
async def test_archived_row_displays_archived_status_when_shown():
    bus = EventBus()
    app = _HostApp(bus)
    async with app.run_test() as pilot:
        await pilot.pause()
        bus.publish(AgentSpawned(info=_info("hidden", archived=True)))
        await pilot.pause()

        table = app.query_one(AgentTable).query_one(DataTable)
        await pilot.press("a")  # show archived
        await pilot.pause()

        cell = table.get_cell("hidden", "status")
        assert "archived" in str(cell)


@pytest.mark.asyncio
async def test_pressing_d_archives_selected_agent_via_manager():
    bus = EventBus()
    infos: dict[str, AgentInfo] = {"a1": _info("a1")}
    manager = _StubManager(bus, infos)
    app = _HostApp(bus, manager=manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        bus.publish(AgentSpawned(info=infos["a1"]))
        await pilot.pause()

        table = app.query_one(AgentTable).query_one(DataTable)
        table.focus()
        await pilot.pause()

        await pilot.press("d")
        await pilot.pause()

        assert manager.calls == [("a1", True)]
        # The stub re-publishes AgentArchiveChanged, so the row should be
        # filtered out of the default (archived-hidden) view.
        assert table.row_count == 0


@pytest.mark.asyncio
async def test_pressing_d_on_archived_agent_unarchives_it():
    bus = EventBus()
    infos: dict[str, AgentInfo] = {"a1": _info("a1", archived=True)}
    manager = _StubManager(bus, infos)
    app = _HostApp(bus, manager=manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        bus.publish(AgentSpawned(info=infos["a1"]))
        await pilot.pause()

        table = app.query_one(AgentTable).query_one(DataTable)
        # Reveal archived agent so cursor can land on it.
        await pilot.press("a")
        await pilot.pause()
        assert table.row_count == 1

        await pilot.press("d")
        await pilot.pause()

        assert manager.calls == [("a1", False)]


@pytest.mark.asyncio
async def test_pressing_d_with_no_rows_is_a_noop():
    bus = EventBus()
    manager = _StubManager(bus, {})
    app = _HostApp(bus, manager=manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one(AgentTable).query_one(DataTable)
        table.focus()
        await pilot.pause()

        await pilot.press("d")
        await pilot.pause()

        assert manager.calls == []


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
