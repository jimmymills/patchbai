import time

from textual.app import ComposeResult
from textual.containers import Container
from textual.widgets import DataTable

from mod_tui.agents.state import AgentInfo
from mod_tui.events import (
    AgentMessageAppended,
    AgentSpawned,
    AgentStateChanged,
    EventBus,
)


class AgentTable(Container):
    """Sortable table of agents — name, status, elapsed, last action, cost."""

    DEFAULT_CSS = """
    AgentTable {
        border: round $surface-lighten-2;
        padding: 0 1;
    }
    AgentTable DataTable {
        height: 1fr;
    }
    """

    COLUMNS = ("name", "status", "elapsed", "last action", "cost")

    def __init__(self, *, event_bus: EventBus | None = None) -> None:
        super().__init__()
        self._bus = event_bus
        # agent_id → row_key for DataTable updates.
        self._rows: dict[str, str] = {}
        # agent_id → last AgentInfo snapshot for re-rendering.
        self._infos: dict[str, AgentInfo] = {}
        # agent_id → most recent message text (last action).
        self._last_actions: dict[str, str] = {}
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

    def on_unmount(self) -> None:
        for u in self._unsubs:
            u()
        self._unsubs = []

    def _on_spawned(self, event: AgentSpawned) -> None:
        info = event.info
        self._infos[info.id] = info
        table = self.query_one(DataTable)
        table.add_row(*self._render_cells(info), key=info.id)
        self._rows[info.id] = info.id  # row key == agent id

    def _on_state(self, event: AgentStateChanged) -> None:
        self._infos[event.info.id] = event.info
        self._refresh_row(event.info.id)

    def _on_msg(self, event: AgentMessageAppended) -> None:
        self._last_actions[event.agent_id] = f"[{event.role}] {event.text[:60]}"
        if event.agent_id in self._infos:
            self._refresh_row(event.agent_id)

    def _refresh_row(self, agent_id: str) -> None:
        if agent_id not in self._rows:
            return
        info = self._infos[agent_id]
        table = self.query_one(DataTable)
        cells = self._render_cells(info)
        for col, value in zip(self.COLUMNS, cells):
            table.update_cell(agent_id, col, value)

    def _render_cells(self, info: AgentInfo) -> tuple:
        # Wrap each cell in Rich Text so values that may contain markup-like
        # text (especially the "last action" cell which echoes tool args)
        # render verbatim rather than tripping the markup parser.
        from rich.text import Text
        elapsed = info.elapsed_seconds()
        elapsed_str = f"{elapsed:5.1f}s"
        last = self._last_actions.get(info.id, "")
        cost_str = f"${info.cost:.4f}"
        return (
            Text(info.name),
            Text(info.state.value),
            Text(elapsed_str),
            Text(last),
            Text(cost_str),
        )
