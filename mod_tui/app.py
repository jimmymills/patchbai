from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.widgets import DataTable

from mod_tui.agents.manager import AgentManager
from mod_tui.agents.sdk_adapter import RealSDKAdapter
from mod_tui.events import EventBus
from mod_tui.layout.defaults import dashboard_layout
from mod_tui.layout.engine import apply as apply_layout
from mod_tui.layout.registry import WidgetRegistry
from mod_tui.layout.spec import LayoutSpec
from mod_tui.orchestrator.session import OrchestratorSession
from mod_tui.persistence.layout_store import load_layout, save_layout
from mod_tui.persistence.transcript_store import OrchestratorTranscript
from mod_tui.widgets.agent_table import AgentTable
from mod_tui.widgets.chrome import CommandBar, StatusBar
from mod_tui.widgets.orchestrator_chat import OrchestratorChat
from mod_tui.widgets.placeholders import ActivityFeed
from mod_tui.widgets.transcript_screen import TranscriptScreen


def build_default_registry() -> WidgetRegistry:
    reg = WidgetRegistry()
    reg.register("OrchestratorChat", OrchestratorChat)
    reg.register("AgentTable", AgentTable)
    reg.register("ActivityFeed", ActivityFeed)
    return reg


class ModTuiApp(App):
    """Plan-2 App: real orchestrator + AgentManager + clickable AgentTable rows."""

    CSS = """
    #panel-area {
        height: 1fr;
    }
    """

    BINDINGS = [
        Binding("/", "focus_command_bar", "command bar", priority=True),
        Binding("ctrl+q", "quit", "quit"),
        Binding("?", "show_help", "help"),
    ]

    def __init__(
        self,
        *,
        cwd: Path | None = None,
        registry: WidgetRegistry | None = None,
        manager: AgentManager | None = None,
        orchestrator: OrchestratorSession | None = None,
    ) -> None:
        super().__init__()
        self.cwd = Path(cwd) if cwd else Path.cwd()
        self.event_bus = EventBus()
        self.registry = registry or build_default_registry()
        self._current_spec: LayoutSpec | None = None
        self.transcript = OrchestratorTranscript(cwd=self.cwd)
        self.orchestrator_history: list[tuple[str, str]] = [
            (e.role, e.text) for e in self.transcript.read_all()
        ]
        self.manager = manager or AgentManager(
            cwd=self.cwd,
            bus=self.event_bus,
            adapter_factory=RealSDKAdapter,
        )
        self.orchestrator = orchestrator or OrchestratorSession(
            cwd=self.cwd,
            bus=self.event_bus,
            manager=self.manager,
        )

    def compose(self) -> ComposeResult:
        yield CommandBar(event_bus=self.event_bus)
        yield Container(id="panel-area")
        yield StatusBar(event_bus=self.event_bus)

    async def on_mount(self) -> None:
        await self.orchestrator.start()
        spec = load_layout(self.cwd) or dashboard_layout()
        await self._apply(spec)

    async def _apply(self, spec: LayoutSpec) -> None:
        area = self.query_one("#panel-area", Container)
        await apply_layout(area, spec, self.registry)
        self._current_spec = spec
        save_layout(self.cwd, spec)

    async def on_unmount(self) -> None:
        await self.orchestrator.stop()
        await self.manager.shutdown()

    async def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        # AgentTable rows use agent_id as their row key.
        agent_id = str(event.row_key.value)
        await self.push_screen(TranscriptScreen(agent_id=agent_id, event_bus=self.event_bus))

    def action_focus_command_bar(self) -> None:
        self.query_one(CommandBar).focus_input()

    def action_show_help(self) -> None:
        self.notify(
            "/ command bar · ctrl-q quit · ? help · click an agent row to view its transcript",
            title="keybindings",
        )
