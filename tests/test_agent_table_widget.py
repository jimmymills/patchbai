import dataclasses
from pathlib import Path

import pytest
from textual.app import App
from textual.widgets import DataTable

from patchbai.agents.state import AgentInfo, AgentState
from patchbai.events import (
    AgentArchiveChanged,
    AgentMessageAppended,
    AgentSpawned,
    AgentStateChanged,
    EventBus,
)
from patchbai.persistence.agents_index import AgentsIndex
from patchbai.widgets.agent_table import AgentTable


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


@pytest.mark.asyncio
async def test_status_cell_uses_orange_for_awaiting_permission():
    from rich.text import Text
    bus = EventBus()
    app = _HostApp(bus)
    async with app.run_test() as pilot:
        await pilot.pause()
        info = _info(state=AgentState.AWAITING_PERMISSION)
        widget = app.query_one(AgentTable)
        cells = widget._render_cells(info)
        status_cell = cells[1]
        assert isinstance(status_cell, Text)
        assert status_cell.plain == "awaiting_permission"
        assert "orange" in str(status_cell.style).lower()


@pytest.mark.asyncio
async def test_seeded_rows_are_in_default_sort_order(tmp_path: Path):
    # Seed mixes states; expect WAITING > RUNNING > ERROR > DONE order.
    idx = AgentsIndex(cwd=tmp_path)
    idx.save([
        _info("d1", state=AgentState.DONE),
        _info("e1", state=AgentState.ERROR),
        _info("r1", state=AgentState.RUNNING),
        _info("w1", state=AgentState.WAITING),
    ])
    bus = EventBus()
    app = _HostApp(bus, cwd=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one(AgentTable).query_one(DataTable)
        keys = [str(row.value) for row in table.rows.keys()]
        assert keys == ["w1", "r1", "e1", "d1"]


@pytest.mark.asyncio
async def test_state_change_running_to_done_moves_row_to_bottom():
    # Two agents start RUNNING; when one finishes, the DONE row should
    # drop below the still-RUNNING row.
    bus = EventBus()
    app = _HostApp(bus)
    async with app.run_test() as pilot:
        await pilot.pause()
        a = _info("a", state=AgentState.RUNNING)
        b = _info("b", state=AgentState.RUNNING)
        bus.publish(AgentSpawned(info=a))
        bus.publish(AgentSpawned(info=b))
        await pilot.pause()

        # Flip "a" to DONE.
        a_done = dataclasses.replace(a, state=AgentState.DONE, ended_at=200.0)
        bus.publish(AgentStateChanged(info=a_done, old_state=AgentState.RUNNING))
        await pilot.pause()

        table = app.query_one(AgentTable).query_one(DataTable)
        keys = [str(row.value) for row in table.rows.keys()]
        # "b" still RUNNING ⇒ priority 1; "a" now DONE ⇒ priority 4.
        assert keys == ["b", "a"]


@pytest.mark.asyncio
async def test_spawn_inserts_at_correct_priority_position():
    # Existing DONE agent on top should be displaced when a WAITING
    # agent spawns.
    bus = EventBus()
    app = _HostApp(bus)
    async with app.run_test() as pilot:
        await pilot.pause()
        bus.publish(AgentSpawned(info=_info("d", state=AgentState.DONE)))
        await pilot.pause()
        bus.publish(AgentSpawned(info=_info("w", state=AgentState.WAITING)))
        await pilot.pause()

        table = app.query_one(AgentTable).query_one(DataTable)
        keys = [str(row.value) for row in table.rows.keys()]
        assert keys == ["w", "d"]


@pytest.mark.asyncio
async def test_archived_row_sinks_to_bottom_when_visible():
    bus = EventBus()
    app = _HostApp(bus)
    async with app.run_test() as pilot:
        await pilot.pause()
        bus.publish(AgentSpawned(info=_info("live", state=AgentState.DONE)))
        bus.publish(AgentSpawned(info=_info("arch", state=AgentState.WAITING,
                                            archived=True)))
        await pilot.pause()

        table = app.query_one(AgentTable).query_one(DataTable)
        await pilot.press("a")  # show archived
        await pilot.pause()

        keys = [str(row.value) for row in table.rows.keys()]
        # Even though "arch" is WAITING (priority 0), it's archived ⇒ last.
        assert keys == ["live", "arch"]


@pytest.mark.asyncio
async def test_message_bumps_agent_to_top_of_its_bucket():
    # Two RUNNING agents; the one that just received a message should
    # rise to the top of the RUNNING bucket via the last_activity
    # tiebreaker. AgentMessageAppended carries no AgentInfo, so we
    # mutate `a.last_activity` in place and rely on _on_msg using the
    # cached info — the same contract AgentSession uses in production.
    bus = EventBus()
    app = _HostApp(bus)
    async with app.run_test() as pilot:
        await pilot.pause()
        a = _info("a", state=AgentState.RUNNING)
        a.last_activity = 100.0
        b = _info("b", state=AgentState.RUNNING)
        b.last_activity = 200.0
        bus.publish(AgentSpawned(info=a))
        bus.publish(AgentSpawned(info=b))
        await pilot.pause()

        table = app.query_one(AgentTable).query_one(DataTable)
        # b is more recent ⇒ b first.
        keys = [str(row.value) for row in table.rows.keys()]
        assert keys == ["b", "a"]

        # Now a gets a message; bump its last_activity past b's first, then
        # publish AgentMessageAppended. _on_msg sees the fresh value via the
        # cached info reference and triggers a re-sort.
        a.last_activity = 300.0
        bus.publish(AgentMessageAppended(
            agent_id="a", role="assistant", text="hello",
        ))
        await pilot.pause()
        keys = [str(row.value) for row in table.rows.keys()]
        assert keys == ["a", "b"]


@pytest.mark.asyncio
async def test_cursor_follows_agent_across_state_change_reorder():
    # Construct a scenario where the focused agent's row index actually
    # changes AND the new row-0 agent is NOT the focused one, so we
    # exercise the move_cursor(row=index) restore branch — Textual's
    # default after clear() resets the cursor to row 0, so a 2-agent
    # setup where the focused agent ends up at row 0 anyway would pass
    # vacuously. Three agents with the focused one at row 1 after the
    # reorder forces the test to depend on the restore wiring.
    bus = EventBus()
    app = _HostApp(bus)
    async with app.run_test() as pilot:
        await pilot.pause()
        a = _info("a", state=AgentState.RUNNING)
        a.last_activity = 100.0  # least recent → starts at row 2
        b = _info("b", state=AgentState.RUNNING)
        b.last_activity = 200.0  # middle → starts at row 1
        c = _info("c", state=AgentState.RUNNING)
        c.last_activity = 300.0  # most recent → starts at row 0
        bus.publish(AgentSpawned(info=a))
        bus.publish(AgentSpawned(info=b))
        bus.publish(AgentSpawned(info=c))
        await pilot.pause()

        widget = app.query_one(AgentTable)
        table = widget.query_one(DataTable)
        table.focus()
        await pilot.pause()
        # Initial order: [c, b, a] — all RUNNING, sorted by last_activity desc.
        keys = [str(row.value) for row in table.rows.keys()]
        assert keys == ["c", "b", "a"]
        # Move cursor to row 2 ("a") so the upcoming reorder will move it
        # to a row that is NOT row 0 (which is where Textual's default lands).
        table.move_cursor(row=2)
        await pilot.pause()
        assert widget._cursor_agent_id() == "a"

        # Flip "c" to DONE — drops it below the RUNNING agents.
        # New order: [b, a, c]. "a" moves from row 2 → row 1.
        # Without the restore branch, Textual resets cursor to row 0 = "b".
        # With the restore branch, cursor follows "a" to row 1.
        c_done = dataclasses.replace(c, state=AgentState.DONE, ended_at=400.0)
        bus.publish(AgentStateChanged(info=c_done, old_state=AgentState.RUNNING))
        await pilot.pause()

        keys = [str(row.value) for row in table.rows.keys()]
        assert keys == ["b", "a", "c"]
        assert widget._cursor_agent_id() == "a"


@pytest.mark.asyncio
async def test_cursor_resets_when_focused_agent_archived_off_screen():
    # Cursor on "a"; archive "a" with archived hidden; cursor's prior
    # agent is gone — table should not crash and should land cursor on
    # whichever row is at index 0 (or have no cursor if empty).
    bus = EventBus()
    infos = {"a": _info("a"), "b": _info("b")}
    manager = _StubManager(bus, infos)
    app = _HostApp(bus, manager=manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        bus.publish(AgentSpawned(info=infos["a"]))
        bus.publish(AgentSpawned(info=infos["b"]))
        await pilot.pause()

        widget = app.query_one(AgentTable)
        table = widget.query_one(DataTable)
        table.focus()
        await pilot.pause()
        # Move cursor to row 0 (whatever sort order produced); we don't
        # care which agent — just that the table doesn't blow up after
        # archiving the one at the cursor.
        focused = widget._cursor_agent_id()
        assert focused in ("a", "b")
        manager.set_archived(focused, archived=True)
        await pilot.pause()

        # Only the un-archived row remains (archived hidden by default).
        assert table.row_count == 1
        # Cursor lands on the surviving (un-archived) row without raising.
        surviving = "b" if focused == "a" else "a"
        assert widget._cursor_agent_id() == surviving
