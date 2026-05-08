from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.widgets import DataTable

from patchfeld.agents.sort import sort_agents
from patchfeld.agents.state import AgentInfo
from patchfeld.events import (
    AgentArchiveChanged,
    AgentFocusRequested,
    AgentMessageAppended,
    AgentSpawned,
    AgentStateChanged,
    EventBus,
)
from patchfeld.persistence.agents_index import AgentsIndex

from patchfeld.agents.state import AgentState as _AgentState

_STATUS_STYLES: dict[_AgentState, str] = {
    _AgentState.IDLE: "dim",
    _AgentState.RUNNING: "green",
    _AgentState.WAITING: "yellow",
    _AgentState.AWAITING_PERMISSION: "orange1",
    _AgentState.DONE: "bold",
    _AgentState.ERROR: "red",
}


class AgentTable(Container):
    """Sortable table of agents — name, status, elapsed, last action, cost.

    Archived agents are hidden by default; press `a` to toggle visibility,
    `d` to archive (or un-archive, when shown) the cursor's agent.
    """

    DEFAULT_BORDER_TITLE = "Agents"

    DEFAULT_CSS = """
    AgentTable {
        border: round $surface-lighten-2;
        padding: 0 1;
    }
    AgentTable DataTable {
        height: 1fr;
    }
    """

    BINDINGS = [
        Binding("d", "toggle_archive", "archive/unarchive"),
        Binding("a", "toggle_show_archived", "show/hide archived"),
    ]

    COLUMNS = ("name", "status", "elapsed", "last action", "cost")

    def __init__(self, *, event_bus: EventBus | None = None) -> None:
        super().__init__()
        self._bus = event_bus
        # agent_id → row_key for DataTable updates. Only contains agents
        # whose row is currently rendered (archived rows are absent when
        # `_show_archived` is False).
        self._rows: dict[str, str] = {}
        # agent_id → last AgentInfo snapshot. This is the canonical source
        # for filtering and re-rendering; it includes archived agents that
        # may not currently have a row in the DataTable.
        self._infos: dict[str, AgentInfo] = {}
        # agent_id → most recent message text (last action).
        self._last_actions: dict[str, str] = {}
        # When False, archived agents are filtered out of the table.
        self._show_archived: bool = False
        self._unsubs: list = []

    def compose(self) -> ComposeResult:
        table = DataTable(zebra_stripes=True, cursor_type="row")
        for col in self.COLUMNS:
            table.add_column(col, key=col)
        yield table

    def on_mount(self) -> None:
        # Seed past agents from disk so a fresh process boot still surfaces
        # the agents the user spawned in the previous session. AgentManager
        # has already reconciled any non-terminal records to ERROR, so what
        # we read here is safe to display as-is.
        cwd = getattr(self.app, "cwd", None)
        if cwd is not None:
            for info in AgentsIndex(cwd=cwd).load():
                if info.id == "orchestrator":
                    continue
                # Just record into _infos; the rebuild below renders rows in order.
                self._infos[info.id] = info
            self._rebuild_sorted()

        bus = self._bus or getattr(self.app, "event_bus", None)
        if bus is None:
            return
        self._unsubs.append(bus.subscribe(AgentSpawned, self._on_spawned))
        self._unsubs.append(bus.subscribe(AgentStateChanged, self._on_state))
        self._unsubs.append(bus.subscribe(AgentMessageAppended, self._on_msg))
        self._unsubs.append(
            bus.subscribe(AgentArchiveChanged, self._on_archive_changed)
        )
        self._unsubs.append(
            bus.subscribe(AgentFocusRequested, self._on_focus_requested)
        )

    def on_unmount(self) -> None:
        for u in self._unsubs:
            u()
        self._unsubs = []

    # --- event handlers ---------------------------------------------------

    def _on_spawned(self, event: AgentSpawned) -> None:
        # Record the new agent and rebuild so it lands at the right priority.
        self._infos[event.info.id] = event.info
        self._rebuild_sorted()

    def _on_state(self, event: AgentStateChanged) -> None:
        # Preserve any archived flag we already know about — AgentStateChanged
        # is emitted by AgentSession with the live info, but the SDK side
        # doesn't touch `archived`, so an existing snapshot's flag is the
        # source of truth.
        prev = self._infos.get(event.info.id)
        if prev is not None and prev.archived and not event.info.archived:
            event.info.archived = True
        self._infos[event.info.id] = event.info
        self._rebuild_sorted()

    def _on_msg(self, event: AgentMessageAppended) -> None:
        self._last_actions[event.agent_id] = f"[{event.role}] {event.text[:60]}"
        if event.agent_id in self._infos:
            # last_activity feeds the sort tiebreaker, so rebuild to let the
            # row bubble up within its bucket and refresh its last-action cell.
            self._rebuild_sorted()

    def _on_archive_changed(self, event: AgentArchiveChanged) -> None:
        self._infos[event.info.id] = event.info
        self._rebuild_sorted()

    def _on_focus_requested(self, event: AgentFocusRequested) -> None:
        """Select the row matching event.agent_id and scroll it into view."""
        agent_id = event.agent_id
        if agent_id not in self._rows:
            return
        try:
            table = self.query_one(DataTable)
        except Exception:
            return
        for index, row_key in enumerate(table.rows.keys()):
            if str(row_key.value) == agent_id:
                table.move_cursor(row=index)
                table.scroll_to(0, index, animate=False)
                return

    # --- actions ----------------------------------------------------------

    def action_toggle_archive(self) -> None:
        manager = getattr(self.app, "manager", None)
        if manager is None:
            return
        agent_id = self._cursor_agent_id()
        if agent_id is None:
            return
        info = self._infos.get(agent_id)
        if info is None:
            return
        manager.set_archived(agent_id, archived=not info.archived)

    def action_toggle_show_archived(self) -> None:
        self._show_archived = not self._show_archived
        self._rebuild_sorted()

    # --- internals --------------------------------------------------------

    def _cursor_agent_id(self) -> str | None:
        table = self.query_one(DataTable)
        if table.row_count == 0:
            return None
        try:
            row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
        except Exception:
            return None
        return None if row_key.value is None else str(row_key.value)

    def _is_visible(self, info: AgentInfo) -> bool:
        return self._show_archived or not info.archived

    def _rebuild_sorted(self) -> None:
        """Clear and re-add rows from `_infos` in default sort order, honoring
        the visibility filter. Preserves the cursor's focused agent across the
        rebuild so a sort-induced reorder doesn't snap the user back to row 0."""
        table = self.query_one(DataTable)

        # Capture cursor agent BEFORE clear(); coordinate_to_cell_key throws
        # after the table is empty.
        cursor_agent_id = self._cursor_agent_id()

        table.clear()
        self._rows.clear()

        visible = [info for info in self._infos.values() if self._is_visible(info)]
        for info in sort_agents(visible):
            table.add_row(*self._render_cells(info), key=info.id)
            self._rows[info.id] = info.id

        # Restore cursor onto the same agent if it's still visible.
        if cursor_agent_id is not None and cursor_agent_id in self._rows:
            for index, row_key in enumerate(table.rows.keys()):
                if str(row_key.value) == cursor_agent_id:
                    table.move_cursor(row=index)
                    break

    def _render_cells(self, info: AgentInfo) -> tuple:
        # Wrap each cell in Rich Text so values that may contain markup-like
        # text (especially the "last action" cell which echoes tool args)
        # render verbatim rather than tripping the markup parser.
        from rich.text import Text
        elapsed = info.elapsed_seconds()
        elapsed_str = f"{elapsed:5.1f}s"
        last = self._last_actions.get(info.id, "")
        cost_str = f"${info.cost:.4f}"
        if info.archived:
            status = "archived"
            status_style = ""
            name = f"{info.name} (archived)"
        else:
            status = info.state.value
            status_style = _STATUS_STYLES.get(info.state, "")
            name = info.name
        return (
            Text(name),
            Text(status, style=status_style),
            Text(elapsed_str),
            Text(last),
            Text(cost_str),
        )
