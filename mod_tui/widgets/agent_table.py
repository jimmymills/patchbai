from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.widgets import DataTable

from mod_tui.agents.state import AgentInfo
from mod_tui.events import (
    AgentArchiveChanged,
    AgentMessageAppended,
    AgentSpawned,
    AgentStateChanged,
    EventBus,
)


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
        bus = self._bus or getattr(self.app, "event_bus", None)
        if bus is None:
            return
        self._unsubs.append(bus.subscribe(AgentSpawned, self._on_spawned))
        self._unsubs.append(bus.subscribe(AgentStateChanged, self._on_state))
        self._unsubs.append(bus.subscribe(AgentMessageAppended, self._on_msg))
        self._unsubs.append(
            bus.subscribe(AgentArchiveChanged, self._on_archive_changed)
        )

    def on_unmount(self) -> None:
        for u in self._unsubs:
            u()
        self._unsubs = []

    # --- event handlers ---------------------------------------------------

    def _on_spawned(self, event: AgentSpawned) -> None:
        info = event.info
        self._infos[info.id] = info
        if self._is_visible(info):
            self._add_row(info)

    def _on_state(self, event: AgentStateChanged) -> None:
        # Preserve any archived flag we already know about — AgentStateChanged
        # is emitted by AgentSession with the live info, but the SDK side
        # doesn't touch `archived`, so an existing snapshot's flag is the
        # source of truth.
        prev = self._infos.get(event.info.id)
        if prev is not None and prev.archived and not event.info.archived:
            event.info.archived = True
        self._infos[event.info.id] = event.info
        self._sync_row(event.info)

    def _on_msg(self, event: AgentMessageAppended) -> None:
        self._last_actions[event.agent_id] = f"[{event.role}] {event.text[:60]}"
        if event.agent_id in self._infos:
            self._sync_row(self._infos[event.agent_id])

    def _on_archive_changed(self, event: AgentArchiveChanged) -> None:
        self._infos[event.info.id] = event.info
        self._sync_row(event.info)

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
        self._rebuild_rows()

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

    def _add_row(self, info: AgentInfo) -> None:
        table = self.query_one(DataTable)
        table.add_row(*self._render_cells(info), key=info.id)
        self._rows[info.id] = info.id  # row key == agent id

    def _remove_row(self, agent_id: str) -> None:
        table = self.query_one(DataTable)
        try:
            table.remove_row(agent_id)
        except Exception:
            return
        self._rows.pop(agent_id, None)

    def _sync_row(self, info: AgentInfo) -> None:
        """Make the table reflect `info` given the current visibility filter:
        add/remove rows as needed, update cells if already present."""
        visible = self._is_visible(info)
        present = info.id in self._rows
        if visible and not present:
            self._add_row(info)
            return
        if not visible and present:
            self._remove_row(info.id)
            return
        if visible and present:
            self._refresh_cells(info)

    def _refresh_cells(self, info: AgentInfo) -> None:
        table = self.query_one(DataTable)
        cells = self._render_cells(info)
        for col, value in zip(self.COLUMNS, cells):
            table.update_cell(info.id, col, value)

    def _rebuild_rows(self) -> None:
        """Clear and re-add rows from `_infos` honoring the visibility flag.
        Used when `_show_archived` toggles."""
        table = self.query_one(DataTable)
        table.clear()
        self._rows.clear()
        for info in self._infos.values():
            if self._is_visible(info):
                self._add_row(info)

    def _render_cells(self, info: AgentInfo) -> tuple:
        # Wrap each cell in Rich Text so values that may contain markup-like
        # text (especially the "last action" cell which echoes tool args)
        # render verbatim rather than tripping the markup parser.
        from rich.text import Text
        elapsed = info.elapsed_seconds()
        elapsed_str = f"{elapsed:5.1f}s"
        last = self._last_actions.get(info.id, "")
        cost_str = f"${info.cost:.4f}"
        status = "archived" if info.archived else info.state.value
        name = f"{info.name} (archived)" if info.archived else info.name
        return (
            Text(name),
            Text(status),
            Text(elapsed_str),
            Text(last),
            Text(cost_str),
        )
