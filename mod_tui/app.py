import secrets
from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding, BindingsMap
from textual.containers import Container
from textual.keys import _character_to_key
from textual.widgets import DataTable, TabbedContent, TabPane

from mod_tui.actions import ActionRegistry
from mod_tui.agents.manager import AgentManager
from mod_tui.agents.sdk_adapter import RealSDKAdapter
from mod_tui.config import ConfigStore
from mod_tui.events import (
    EventBus, OpenResumePicker, TabAdded, TabClosed, TabSwitched,
)
from mod_tui.layout.defaults import dashboard_layout
from mod_tui.layout.engine import apply as apply_layout
from mod_tui.layout.registry import WidgetRegistry
from mod_tui.layout.spec import LayoutSpec
from mod_tui.orchestrator.session import OrchestratorSession
from mod_tui.persistence.layouts_store import NamedLayoutsStore
from mod_tui.persistence.paths import global_config_dir
from mod_tui.persistence.workspace_store import (
    load_workspace as load_local_workspace,
    save_workspace as save_local_workspace,
)
from mod_tui.persistence.agents_index import AgentsIndex
from mod_tui.widgets.agent_table import AgentTable
from mod_tui.widgets.agent_transcript import AgentTranscript
from mod_tui.widgets.chrome import CommandBar, StatusBar
from mod_tui.widgets.diff_viewer import DiffViewer
from mod_tui.widgets.file_tree import FileTree
from mod_tui.widgets.log_tail import LogTail
from mod_tui.widgets.notebook import Notebook
from mod_tui.widgets.file_viewer import FileViewer
from mod_tui.widgets.markdown import Markdown
from mod_tui.persistence.orchestrator_sessions import OrchestratorSessionsIndex
from mod_tui.widgets.history_screen import HistoryScreen
from mod_tui.widgets.layout_switcher import LayoutSwitcherScreen
from mod_tui.widgets.resume_screen import ResumeScreen
from mod_tui.widgets.new_tab_screen import NewTabScreen
from mod_tui.widgets.orchestrator_chat import OrchestratorChat
from mod_tui.widgets.placeholders import ActivityFeed
from mod_tui.widgets.terminal import Terminal
from mod_tui.widgets.transcript_screen import TranscriptScreen
from mod_tui.workspace.spec import Tab, Workspace, workspace_from_layout, _contains_chat


def build_default_registry() -> WidgetRegistry:
    reg = WidgetRegistry()
    reg.register("OrchestratorChat", OrchestratorChat)
    reg.register("AgentTable", AgentTable)
    reg.register(
        "AgentTranscript", AgentTranscript,
        description=(
            "Live, scrolling transcript for one agent with a bottom input "
            "that sends DirectMessageToAgent for that `agent_id`. Mount "
            "this in a panel when the user wants to converse with a "
            "specific child agent without going through the orchestrator."
        ),
        props_schema={"agent_id": str},
    )
    reg.register("ActivityFeed", ActivityFeed)
    reg.register(
        "Markdown", Markdown,
        description="Renders markdown from `source` (string) or `file_path`.",
        props_schema={"source": str, "file_path": str},
    )
    reg.register(
        "FileViewer", FileViewer,
        description=(
            "Read-only syntax-highlighted file display. Pass `file_path` for "
            "an initial file. Pass `follow_selection: true` to subscribe to "
            "FileSelected events from a FileTree panel and reload on click."
        ),
        props_schema={"file_path": str, "follow_selection": bool},
    )
    reg.register(
        "FileTree", FileTree,
        description=(
            "Directory tree starting at `path`. Publishes a FileSelected "
            "event on the bus when the user selects a file — pair with a "
            "FileViewer(follow_selection=True) to see contents."
        ),
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
    reg.register(
        "Notebook", Notebook,
        description=(
            "Editable scratch buffer; persists to <cwd>/.mod_tui/scratch/<name>.md."
        ),
        props_schema={"name": str},
    )
    reg.register(
        "Terminal", Terminal,
        description=(
            "Real PTY in a panel. Use this for an interactive `claude` CLI "
            "session inside mod_tui — anything typed here is OPAQUE to the "
            "orchestrator (intentional escape-hatch behavior). Optional "
            "`command` (argv), `cwd`, and `env` props."
        ),
        props_schema={"command": list, "cwd": str, "env": dict},
    )
    return reg


class ModTuiApp(App):
    """Plan-4 App: layout + config mutability via orchestrator MCP tools."""

    CSS = """
    #app-tabs {
        height: 1fr;
    }
    """

    # Default bindings applied before any config-loaded bindings.
    BINDINGS = [
        Binding("/", "focus_command_bar", "command bar"),
        Binding("ctrl+q", "quit", "quit"),
        Binding("ctrl+h", "open_history", "history"),
        Binding("ctrl+l", "open_layout_switcher", "layouts"),
        Binding("?", "show_help", "help"),
        Binding("ctrl+t", "new_tab", "new tab", priority=True),
        Binding("ctrl+w", "close_active_tab", "close tab", priority=True),
        Binding("ctrl+1", "switch_tab_index(0)", "tab 1"),
        Binding("ctrl+2", "switch_tab_index(1)", "tab 2"),
        Binding("ctrl+3", "switch_tab_index(2)", "tab 3"),
        Binding("ctrl+4", "switch_tab_index(3)", "tab 4"),
        Binding("ctrl+5", "switch_tab_index(4)", "tab 5"),
        Binding("ctrl+6", "switch_tab_index(5)", "tab 6"),
        Binding("ctrl+7", "switch_tab_index(6)", "tab 7"),
        Binding("ctrl+8", "switch_tab_index(7)", "tab 8"),
        Binding("ctrl+9", "switch_tab_index(8)", "tab 9"),
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
        self._workspace: Workspace | None = None
        self._active_tab_id: str | None = None
        self._current_layout_name: str | None = None  # last `load_layout` name
        self._tab_focus_snapshots: dict[str, str] = {}  # tab_id -> last focused panel id
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
            current_layout=lambda: self._active_layout(),
            app=self,
        )
        # Production opts in to LLM-summarized session titles.
        self.orchestrator._auto_title_enabled = True

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
            # priority=True bindings fire before the focused widget gets the
            # key, which prevents Inputs from receiving printable characters
            # (e.g. "/", "?"). Only modifier-combo keys (ctrl+/alt+/shift+)
            # need priority — single-character bindings like "/" must not,
            # so they reach the focused Input as text.
            is_modifier_combo = "+" in key
            # Normalize single non-alphanumeric characters to their Textual key
            # name (e.g. "~" → "tilde") so that pilot.press / live key events
            # match the binding key stored in key_to_bindings.
            if len(key) == 1 and not key.isalnum():
                key = _character_to_key(key)
            self._bindings._add_binding(
                Binding(
                    key, f"dispatch('{b.action}')", b.action,
                    priority=is_modifier_combo,
                )
            )

        # Ask Textual to refresh any Footer / binding-display widgets.
        try:
            self.refresh_bindings()
        except AttributeError:
            pass

    # --- tab workspace-mutation surface ------------------------------------

    def _generate_tab_id(self) -> str:
        """Short, collision-checked id."""
        existing = {t.id for t in (self._workspace.tabs if self._workspace else [])}
        while True:
            candidate = secrets.token_hex(3)  # 6 hex chars
            if candidate not in existing:
                return candidate

    def _default_seed_layout(self) -> LayoutSpec:
        """Layout used when add_tab is called with no layout arg.

        If the workspace already has chat in another tab, seed with a chat-less
        ActivityFeed (most 'add a new tab' requests will be followed by a
        set_layout). Otherwise seed with an OrchestratorChat panel."""
        has_chat = False
        if self._workspace is not None:
            has_chat = any(_contains_chat(t.layout.layout) for t in self._workspace.tabs)
        if has_chat:
            return LayoutSpec.model_validate({
                "version": 1,
                "layout": {"id": "feed", "widget": "ActivityFeed"},
            })
        return LayoutSpec.model_validate({
            "version": 1,
            "layout": {"id": "orch", "widget": "OrchestratorChat"},
        })

    async def close_tab(self, tab_id: str) -> dict:
        """Close a tab. Returns a small result dict; never raises on bad input."""
        if self._workspace is None:
            return {"error": "workspace_not_initialized"}
        ws = self._workspace
        tabs = ws.tabs
        target = next((t for t in tabs if t.id == tab_id), None)
        if target is None:
            return {"error": "unknown_tab_id"}
        if len(tabs) == 1:
            return {"error": "would_leave_zero_tabs"}
        remaining = [t for t in tabs if t.id != tab_id]
        if not any(_contains_chat(t.layout.layout) for t in remaining):
            return {"error": "would_leave_no_chat",
                    "suggestion": "add OrchestratorChat to another tab before closing this one"}
        # Determine new active: previous tab, or the first remaining if we close index 0.
        if self._active_tab_id == tab_id:
            idx = next(i for i, t in enumerate(tabs) if t.id == tab_id)
            fallback_idx = max(0, idx - 1)
            new_active = remaining[min(fallback_idx, len(remaining) - 1)].id
        else:
            new_active = self._active_tab_id  # type: ignore[assignment]
        self._workspace = Workspace.model_validate({
            "version": ws.version,
            "tabs": [t.model_dump(mode="json") for t in remaining],
            "active": new_active,
        })
        self._tab_focus_snapshots.pop(tab_id, None)
        # Update _active_tab_id BEFORE awaiting remove_pane: removing the
        # active pane can fire TabActivated synchronously, and the handler
        # short-circuits when new_active == self._active_tab_id. Without this
        # ordering the handler would run with stale state.
        if self._active_tab_id == tab_id:
            self._active_tab_id = new_active
        tc = self.query_one("#app-tabs", TabbedContent)
        await tc.remove_pane(f"tab-{tab_id}")
        if tc.active != f"tab-{new_active}":
            tc.active = f"tab-{new_active}"
        save_local_workspace(self.cwd, self._workspace)
        self.event_bus.publish(TabClosed(tab_id=tab_id))
        return {"closed": tab_id, "new_active": new_active}

    async def add_tab(self, title: str, layout: LayoutSpec, *, activate: bool = True) -> str:
        """Append a new tab. Returns the new tab id. Updates persistence."""
        if self._workspace is None:
            raise RuntimeError("workspace not yet initialized")
        ws = self._workspace
        new_id = self._generate_tab_id()
        new_tab = Tab(id=new_id, title=title, layout=layout)
        new_ws = Workspace.model_validate({
            "version": ws.version,
            "tabs": [t.model_dump(mode="json") for t in ws.tabs] + [new_tab.model_dump(mode="json")],
            "active": new_id if activate else ws.active,
        })
        self._workspace = new_ws
        # Mount the new pane and apply its layout.
        tc = self.query_one("#app-tabs", TabbedContent)
        pane = TabPane(title, Container(id=f"panel-area-{new_id}"), id=f"tab-{new_id}")
        await tc.add_pane(pane)
        area = self.query_one(f"#panel-area-{new_id}", Container)
        await apply_layout(area, layout, self.registry, layout_name=None)
        if activate:
            tc.active = f"tab-{new_id}"
            self._active_tab_id = new_id
        save_local_workspace(self.cwd, self._workspace)
        self.event_bus.publish(TabAdded(tab_id=new_id, title=title))
        return new_id

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
            "/ command bar · ctrl-q quit · ctrl-h history · ctrl-l layouts · "
            "ctrl-pgup/pgdn prev/next tab · ctrl-1..9 tab N · ctrl-t new tab · "
            "ctrl-w close tab · /reset new · /resume past · /rename title · ? help",
            title="keybindings",
        )

    def action_switch_tab_index(self, idx: int) -> None:
        if self._workspace is None:
            return
        if idx < 0 or idx >= len(self._workspace.tabs):
            return  # quietly no-op
        target = self._workspace.tabs[idx].id
        tc = self.query_one("#app-tabs", TabbedContent)
        tc.active = f"tab-{target}"

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

    def action_new_tab(self) -> None:
        import asyncio as _asyncio

        def _on_picked(title: str | None) -> None:
            if not title:
                return
            layout = self._default_seed_layout()
            _asyncio.create_task(self.add_tab(title, layout, activate=True))

        self.push_screen(NewTabScreen(), _on_picked)

    async def action_close_active_tab(self) -> None:
        if self._active_tab_id is None:
            return
        result = await self.close_tab(self._active_tab_id)
        if "error" in result:
            self.notify(f"can't close tab: {result['error']}", severity="warning")

    def _on_open_resume_picker(self, event) -> None:
        import asyncio as _asyncio
        index = OrchestratorSessionsIndex(cwd=self.cwd)

        def _on_picked(session_id: str | None) -> None:
            if session_id:
                _asyncio.create_task(self.orchestrator.resume(session_id))

        self.push_screen(ResumeScreen(index=index), _on_picked)

    # --- helpers -----------------------------------------------------------

    def _active_layout(self) -> LayoutSpec | None:
        if self._workspace is None or self._active_tab_id is None:
            return None
        for t in self._workspace.tabs:
            if t.id == self._active_tab_id:
                return t.layout
        return None

    def _load_or_seed_workspace(self) -> Workspace:
        """Load workspace.json, fall back to migrating layout.json, fall back
        to seeding from the built-in dashboard."""
        ws = load_local_workspace(self.cwd)
        if ws is not None:
            return ws
        # Migration: legacy layout.json -> single-tab workspace.
        from mod_tui.persistence.layout_store import load_layout as _load_legacy
        legacy = _load_legacy(self.cwd)
        if legacy is not None:
            return workspace_from_layout(legacy, tab_id="default", title="default")
        return workspace_from_layout(dashboard_layout(), tab_id="default", title="default")

    async def _mount_workspace(self, ws: Workspace) -> None:
        tc = self.query_one("#app-tabs", TabbedContent)
        # Build one TabPane per Tab, each containing a panel-area-<id> Container.
        new_panes = []
        for t in ws.tabs:
            new_panes.append(
                TabPane(t.title, Container(id=f"panel-area-{t.id}"), id=f"tab-{t.id}")
            )
        await tc.clear_panes()
        for pane in new_panes:
            await tc.add_pane(pane)
        # Apply each tab's layout to its container, eagerly (persistent semantics).
        for t in ws.tabs:
            area = self.query_one(f"#panel-area-{t.id}", Container)
            try:
                await apply_layout(area, t.layout, self.registry, layout_name=None)
            except Exception:
                # Apply errors already publish LayoutFailed; swallow here so one
                # bad tab doesn't block the rest of the workspace from booting.
                pass
        tc.active = f"tab-{ws.active}"

    async def _apply_to_tab(
        self, tab_id: str | None, spec: LayoutSpec,
        *, layout_name: str | None = None,
    ) -> None:
        if tab_id is None or self._workspace is None:
            return
        # Build candidate workspace and validate FIRST (atomic). model_copy
        # bypasses the model_validator, so we round-trip through model_validate
        # to catch invariant breaks (e.g., user removed the only chat) before
        # touching the live UI or persistence.
        candidate = Workspace.model_validate({
            "version": self._workspace.version,
            "tabs": [
                {**t.model_dump(mode="json"), "layout": spec.model_dump(mode="json")}
                if t.id == tab_id else t.model_dump(mode="json")
                for t in self._workspace.tabs
            ],
            "active": self._workspace.active,
        })
        # Validation passed → commit memory, then UI, then disk.
        self._workspace = candidate
        area = self.query_one(f"#panel-area-{tab_id}", Container)
        await apply_layout(area, spec, self.registry, layout_name=layout_name)
        self._current_layout_name = layout_name
        save_local_workspace(self.cwd, self._workspace)

    # --- tab activation handler --------------------------------------------

    def on_tabbed_content_tab_activated(
        self, event: TabbedContent.TabActivated,
    ) -> None:
        """Triggered by TabbedContent when the user (or code) switches the active
        pane. Updates workspace state, persists, fires our TabSwitched event, and
        restores focus to the tab's last-focused panel id."""
        if self._workspace is None:
            return
        # event.tab.id carries the internal ContentTab prefix ("--content-tab-tab-logs");
        # event.pane.id is the TabPane id we set ("tab-logs"), which is what we want.
        pane_id = event.pane.id if event.pane is not None else None
        if not pane_id or not pane_id.startswith("tab-"):
            return
        new_active = pane_id[len("tab-"):]
        if new_active == self._active_tab_id:
            return
        if self._active_tab_id is not None:
            try:
                focused = self.focused
                if focused is not None and focused.id and focused.id.startswith("panel-"):
                    self._tab_focus_snapshots[self._active_tab_id] = focused.id[len("panel-"):]
            except Exception:
                pass
        self._active_tab_id = new_active
        # model_copy bypasses model_validator. Safe here because TabActivated
        # only fires for panes that already exist in self._workspace.tabs, so
        # the "active id must be in tabs" invariant cannot be violated.
        ws = self._workspace.model_copy(update={"active": new_active})
        self._workspace = ws
        save_local_workspace(self.cwd, ws)
        title = next((t.title for t in ws.tabs if t.id == new_active), new_active)
        self.event_bus.publish(TabSwitched(tab_id=new_active, title=title))
        target_tab = next((t for t in ws.tabs if t.id == new_active), None)
        target_panel_id = (
            self._tab_focus_snapshots.get(new_active)
            or (target_tab.layout.focus if target_tab else None)
        )
        if target_panel_id:
            try:
                self.query_one(f"#panel-{target_panel_id}").focus()
            except Exception:
                pass

    # --- composition & lifecycle -------------------------------------------

    def compose(self) -> ComposeResult:
        yield CommandBar(event_bus=self.event_bus)
        yield TabbedContent(id="app-tabs")
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
        self.event_bus.subscribe(OpenResumePicker, self._on_open_resume_picker)
        ws = self._load_or_seed_workspace()
        self._workspace = ws
        self._active_tab_id = ws.active
        await self._mount_workspace(ws)
        save_local_workspace(self.cwd, ws)

    async def _orchestrator_apply_layout(
        self, spec: LayoutSpec,
        *, layout_name: str | None = None, tab_id: str | None = None,
    ) -> None:
        target = tab_id or self._active_tab_id
        await self._apply_to_tab(target, spec, layout_name=layout_name)

    async def on_unmount(self) -> None:
        await self.orchestrator.stop()
        await self.manager.shutdown()

    async def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if isinstance(self.screen, (HistoryScreen, LayoutSwitcherScreen, ResumeScreen)):
            return
        agent_id = str(event.row_key.value)
        await self.push_screen(TranscriptScreen(agent_id=agent_id, event_bus=self.event_bus))
