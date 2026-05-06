from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container

from mod_tui.events import EventBus
from mod_tui.layout.defaults import dashboard_layout
from mod_tui.layout.engine import apply as apply_layout
from mod_tui.layout.registry import WidgetRegistry
from mod_tui.layout.spec import LayoutSpec
from mod_tui.persistence.layout_store import load_layout, save_layout
from mod_tui.widgets.chrome import CommandBar, StatusBar
from mod_tui.widgets.orchestrator_chat import OrchestratorChat
from mod_tui.widgets.placeholders import ActivityFeed, AgentTable


def build_default_registry() -> WidgetRegistry:
    reg = WidgetRegistry()
    reg.register("OrchestratorChat", OrchestratorChat)
    reg.register("AgentTable", AgentTable)
    reg.register("ActivityFeed", ActivityFeed)
    return reg


class ModTuiApp(App):
    """Walking-skeleton App. Real Claude Agent SDK wiring lives in plan 2."""

    CSS = """
    #panel-area {
        height: 1fr;
    }
    """

    # Tab/shift-tab cycle focus via Textual's built-in focus chain — no
    # explicit binding needed. ctrl-h (history) and ctrl-l (layout switcher)
    # ship in plan 4 alongside the features they open.
    BINDINGS = [
        Binding("/", "focus_command_bar", "command bar"),
        Binding("ctrl+q", "quit", "quit"),
        Binding("?", "show_help", "help"),
    ]

    def __init__(self, *, cwd: Path | None = None,
                 registry: WidgetRegistry | None = None) -> None:
        super().__init__()
        self.cwd = Path(cwd) if cwd else Path.cwd()
        self.event_bus = EventBus()
        self.registry = registry or build_default_registry()
        self._current_spec: LayoutSpec | None = None

    def compose(self) -> ComposeResult:
        yield CommandBar(event_bus=self.event_bus)
        yield Container(id="panel-area")
        yield StatusBar(event_bus=self.event_bus)

    async def on_mount(self) -> None:
        spec = load_layout(self.cwd) or dashboard_layout()
        await self._apply(spec)

    async def _apply(self, spec: LayoutSpec) -> None:
        area = self.query_one("#panel-area", Container)
        await apply_layout(area, spec, self.registry)
        self._current_spec = spec
        save_layout(self.cwd, spec)

    def action_focus_command_bar(self) -> None:
        self.query_one(CommandBar).focus_input()

    def action_show_help(self) -> None:
        self.notify(
            "/ command bar · ctrl-q quit · ? help",
            title="keybindings",
        )
