from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding, BindingsMap
from textual.containers import Container
from textual.keys import _character_to_key
from textual.widgets import DataTable

from mod_tui.actions import ActionRegistry
from mod_tui.agents.manager import AgentManager
from mod_tui.agents.sdk_adapter import RealSDKAdapter
from mod_tui.config import ConfigStore
from mod_tui.events import EventBus
from mod_tui.layout.defaults import dashboard_layout
from mod_tui.layout.engine import apply as apply_layout
from mod_tui.layout.registry import WidgetRegistry
from mod_tui.layout.spec import LayoutSpec
from mod_tui.orchestrator.session import OrchestratorSession
from mod_tui.persistence.layout_store import load_layout as load_local_layout
from mod_tui.persistence.layout_store import save_layout as save_local_layout
from mod_tui.persistence.layouts_store import NamedLayoutsStore
from mod_tui.persistence.paths import global_config_dir
from mod_tui.persistence.agents_index import AgentsIndex
from mod_tui.persistence.transcript_store import OrchestratorTranscript
from mod_tui.widgets.agent_table import AgentTable
from mod_tui.widgets.chrome import CommandBar, StatusBar
from mod_tui.widgets.diff_viewer import DiffViewer
from mod_tui.widgets.file_tree import FileTree
from mod_tui.widgets.log_tail import LogTail
from mod_tui.widgets.file_viewer import FileViewer
from mod_tui.widgets.markdown import Markdown
from mod_tui.widgets.history_screen import HistoryScreen
from mod_tui.widgets.layout_switcher import LayoutSwitcherScreen
from mod_tui.widgets.orchestrator_chat import OrchestratorChat
from mod_tui.widgets.placeholders import ActivityFeed
from mod_tui.widgets.transcript_screen import TranscriptScreen


def build_default_registry() -> WidgetRegistry:
    reg = WidgetRegistry()
    reg.register("OrchestratorChat", OrchestratorChat)
    reg.register("AgentTable", AgentTable)
    reg.register("ActivityFeed", ActivityFeed)
    reg.register(
        "Markdown", Markdown,
        description="Renders markdown from `source` (string) or `file_path`.",
        props_schema={"source": str, "file_path": str},
    )
    reg.register(
        "FileViewer", FileViewer,
        description="Read-only syntax-highlighted file display.",
        props_schema={"file_path": str},
    )
    reg.register(
        "FileTree", FileTree,
        description="Directory tree starting at `path`.",
        props_schema={"path": str},
    )
    reg.register(
        "DiffViewer", DiffViewer,
        description=(
            "Unified-diff viewer. Pass a precomputed `diff` string, OR pass "
            "`before` + `after` strings for unified-diff computation."
        ),
        props_schema={"diff": str, "before": str, "after": str},
    )
    reg.register(
        "LogTail", LogTail,
        description=(
            "Tails an arbitrary file. Polls every 250ms. Optional "
            "`tail_lines` controls how much of the existing tail is shown."
        ),
        props_schema={"file_path": str, "tail_lines": int},
    )
    return reg


class ModTuiApp(App):
    """Plan-4 App: layout + config mutability via orchestrator MCP tools."""

    CSS = """
    #panel-area {
        height: 1fr;
    }
    """

    # Default bindings applied before any config-loaded bindings.
    BINDINGS = [
        Binding("/", "focus_command_bar", "command bar", priority=True),
        Binding("ctrl+q", "quit", "quit"),
        Binding("ctrl+h", "open_history", "history"),
        Binding("ctrl+l", "open_layout_switcher", "layouts"),
        Binding("?", "show_help", "help"),
    ]

    def __init__(
        self,
        *,
        cwd: Path | None = None,
        registry: WidgetRegistry | None = None,
        manager: AgentManager | None = None,
        orchestrator: OrchestratorSession | None = None,
        global_dir: Path | None = None,
    ) -> None:
        super().__init__()
        self.cwd = Path(cwd) if cwd else Path.cwd()
        self.event_bus = EventBus()
        self.registry = registry or build_default_registry()
        self._current_spec: LayoutSpec | None = None
        self._current_layout_name: str | None = None
        self.transcript = OrchestratorTranscript(cwd=self.cwd)
        self.orchestrator_history: list[tuple[str, str]] = [
            (e.role, e.text) for e in self.transcript.read_all()
        ]
        self._global_dir = Path(global_dir) if global_dir else global_config_dir()
        self.config_store = ConfigStore(global_dir=self._global_dir)
        self.layouts_store = NamedLayoutsStore(global_dir=self._global_dir)
        self.actions_registry = ActionRegistry()
        self._register_actions()
        self.manager = manager or AgentManager(
            cwd=self.cwd,
            bus=self.event_bus,
            adapter_factory=RealSDKAdapter,
        )
        self.orchestrator = orchestrator or OrchestratorSession(
            cwd=self.cwd,
            bus=self.event_bus,
            manager=self.manager,
            apply_layout=self._orchestrator_apply_layout,
            layouts_store=self.layouts_store,
            config_store=self.config_store,
            actions=self.actions_registry,
            rebind_keys=self._rebind_keys,
            widget_registry=self.registry,
        )

    # --- action registration -----------------------------------------------

    def _register_actions(self) -> None:
        self.actions_registry.register(
            "focus_command_bar", self.action_focus_command_bar,
            description="Move focus to the top command bar.", args_schema={},
        )
        self.actions_registry.register(
            "focus_orchestrator",
            lambda: self._focus_panel("orch"),
            description="Focus the orchestrator chat panel.", args_schema={},
        )
        self.actions_registry.register(
            "focus_panel",
            lambda panel_id: self._focus_panel(panel_id),
            description="Focus a specific panel by id.", args_schema={"panel_id": str},
        )
        self.actions_registry.register(
            "cycle_focus", self.action_focus_next,
            description="Move focus to the next focusable widget.", args_schema={},
        )
        self.actions_registry.register(
            "quit", self.action_quit,
            description="Quit the application.", args_schema={},
        )
        self.actions_registry.register(
            "show_help", self.action_show_help,
            description="Show the keybindings help notification.", args_schema={},
        )
        self.actions_registry.register(
            "open_history", self.action_open_history,
            description="Open the agent history modal.", args_schema={},
        )
        self.actions_registry.register(
            "open_layout_switcher", self.action_open_layout_switcher,
            description="Open the saved-layouts switcher modal.", args_schema={},
        )

    def _focus_panel(self, panel_id: str) -> None:
        try:
            self.query_one(f"#panel-{panel_id}").focus()
        except Exception:
            pass

    # --- dynamic bindings --------------------------------------------------

    def _rebind_keys(self) -> None:
        """Load bindings from config and apply them at runtime.

        Textual 8.x: _bindings is an instance-level BindingsMap set during
        DOMNode.__init__ from the class-level _merged_bindings cache. We
        reset the instance's BindingsMap to a fresh copy of the class-level
        defaults, then layer the config bindings on top.
        """
        # Start from a fresh copy of the class-level merged bindings
        # (which includes BINDINGS declared above).
        base = type(self)._merged_bindings
        if base is not None:
            self._bindings = base.copy()
        else:
            self._bindings = BindingsMap(type(self).BINDINGS)

        # Layer config bindings on top.
        cfg = self.config_store.load()
        for key, b in cfg.bindings.items():
            # Normalize single non-alphanumeric characters to their Textual key
            # name (e.g. "~" → "tilde") so that pilot.press / live key events
            # match the binding key stored in key_to_bindings.
            if len(key) == 1 and not key.isalnum():
                key = _character_to_key(key)
            self._bindings._add_binding(
                Binding(key, f"dispatch('{b.action}')", b.action, priority=True)
            )

        # Ask Textual to refresh any Footer / binding-display widgets.
        try:
            self.refresh_bindings()
        except AttributeError:
            pass

    async def action_dispatch(self, name: str) -> None:
        # Look up the action and call it. If it returns a coroutine (async
        # actions like action_quit / action_open_history / action_open_layout_switcher),
        # await it so the side-effect actually runs.
        import asyncio as _asyncio
        try:
            spec = self.actions_registry.get(name)
        except KeyError:
            return
        result = spec.callable()
        if _asyncio.iscoroutine(result):
            await result

    # --- action handlers ---------------------------------------------------

    def action_focus_command_bar(self) -> None:
        self.query_one(CommandBar).focus_input()

    def action_show_help(self) -> None:
        self.notify(
            "/ command bar · ctrl-q quit · ctrl-h history · ctrl-l layouts · ? help",
            title="keybindings",
        )

    def action_open_history(self) -> None:
        # push_screen with a callback avoids the worker requirement that
        # push_screen_wait imposes. The callback fires when the modal dismisses.
        idx = AgentsIndex(cwd=self.cwd)

        def _on_picked(agent_id: str | None) -> None:
            if agent_id:
                self.push_screen(
                    TranscriptScreen(agent_id=agent_id, event_bus=self.event_bus)
                )

        self.push_screen(HistoryScreen(index=idx), _on_picked)

    def action_open_layout_switcher(self) -> None:
        import asyncio as _asyncio

        def _on_picked(name: str | None) -> None:
            if not name:
                return
            spec = self.layouts_store.load(name)
            if spec is None:
                return
            # Apply is async; schedule it on the running loop.
            _asyncio.create_task(self._orchestrator_apply_layout(spec, layout_name=name))

        self.push_screen(LayoutSwitcherScreen(store=self.layouts_store), _on_picked)

    # --- composition & lifecycle -------------------------------------------

    def compose(self) -> ComposeResult:
        yield CommandBar(event_bus=self.event_bus)
        yield Container(id="panel-area")
        yield StatusBar(event_bus=self.event_bus)

    async def on_mount(self) -> None:
        self._rebind_keys()
        # Seed the built-in dashboard as the named layout "default" if no
        # such file exists yet, so the user can always get back to the
        # canonical layout via ctrl-l or `load_layout("default")`. We only
        # seed once — if the user re-saves "default" with their own arrangement,
        # we leave it alone.
        if self.layouts_store.load("default") is None:
            self.layouts_store.save("default", dashboard_layout())
        await self.orchestrator.start()
        spec = load_local_layout(self.cwd) or dashboard_layout()
        await self._apply(spec)

    async def _orchestrator_apply_layout(
        self, spec: LayoutSpec, *, layout_name: str | None = None
    ) -> None:
        await self._apply(spec, layout_name=layout_name)

    async def _apply(self, spec: LayoutSpec, *, layout_name: str | None = None) -> None:
        area = self.query_one("#panel-area", Container)
        await apply_layout(area, spec, self.registry, layout_name=layout_name)
        self._current_spec = spec
        self._current_layout_name = layout_name
        save_local_layout(self.cwd, spec)

    async def on_unmount(self) -> None:
        await self.orchestrator.stop()
        await self.manager.shutdown()

    async def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if isinstance(self.screen, (HistoryScreen, LayoutSwitcherScreen)):
            return
        agent_id = str(event.row_key.value)
        await self.push_screen(TranscriptScreen(agent_id=agent_id, event_bus=self.event_bus))
