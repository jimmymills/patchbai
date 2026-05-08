import logging
import secrets
from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding, BindingsMap
from textual.containers import Container
from textual.keys import _character_to_key
from textual.widgets import DataTable, TabbedContent, TabPane

from patchbai.actions import ActionRegistry
from patchbai.activity.log import ActivityLog
from patchbai.agents.manager import AgentManager
from patchbai.agents.sdk_adapter import RealSDKAdapter
from patchbai.config import ConfigStore
from patchbai.events import (
    AgentSpawned, AgentStateChanged, AgentTokensTouched, EventBus, LayoutResized,
    OpenResumePicker, PermissionRequested, StatsUpdated, TabAdded, TabClosed, TabSwitched,
)
from patchbai.layout.defaults import dashboard_layout
from patchbai.layout.engine import apply as apply_layout
from patchbai.layout.local_widgets import LocalWidgetLoader, LoadOutcome
from patchbai.layout.registry import WidgetRegistry
from patchbai.layout.spec import LayoutSpec
from patchbai.orchestrator.session import OrchestratorSession
from patchbai.persistence.layouts_store import NamedLayoutsStore
from patchbai.persistence.themes_store import NamedThemesStore
from patchbai.persistence.paths import global_config_dir, local_widgets_dir
from patchbai.theme.engine import _EXTRA_CSS_KEY, apply_theme, palette_from_textual_theme
from patchbai.theme.spec import ThemeSpec
from patchbai.persistence.workspace_store import (
    load_workspace as load_local_workspace,
    save_workspace as save_local_workspace,
)
from patchbai.persistence.agents_index import AgentsIndex
from patchbai.widgets.agent_table import AgentTable
from patchbai.widgets.agent_transcript import AgentTranscript
from patchbai.widgets.chrome import CommandBar, StatusBar
from patchbai.widgets.diff_viewer import DiffViewer
from patchbai.widgets.file_editor import FileEditor
from patchbai.widgets.file_tree import FileTree
from patchbai.widgets.log_tail import LogTail
from patchbai.widgets.notebook import Notebook
from patchbai.widgets.file_viewer import FileViewer
from patchbai.widgets.markdown import Markdown
from patchbai.widgets.system_usage import SystemUsage
from patchbai.persistence.orchestrator_sessions import OrchestratorSessionsIndex
from patchbai.widgets.history_screen import HistoryScreen
from patchbai.widgets.layout_switcher import LayoutSwitcherScreen
from patchbai.widgets.resume_screen import ResumeScreen
from patchbai.widgets.theme_switcher import ThemeSwitcherScreen
from patchbai.widgets.new_tab_screen import NewTabScreen
from patchbai.widgets.orchestrator_chat import OrchestratorChat
from patchbai.widgets.activity_feed import ActivityFeed
from patchbai.widgets.terminal import Terminal
from patchbai.widgets.transcript_screen import TranscriptScreen
from patchbai.workspace.spec import Tab, Workspace, workspace_from_layout, _contains_chat
from patchbai.agents.permission_grants import PermissionGrants


log = logging.getLogger(__name__)


def _resolve_container(root_layout: dict, parent_path: tuple[int, ...]) -> dict | None:
    """Walk `root_layout` (a dict shaped like LayoutSpec.layout) following
    `parent_path` (each step indexes into `children`) and return the parent
    container dict. Returns None if the path doesn't resolve to a node with
    a `children` list."""
    node = root_layout
    for idx in parent_path:
        children = node.get("children")
        if not isinstance(children, list) or idx >= len(children):
            return None
        node = children[idx]
    if not isinstance(node.get("children"), list):
        return None
    return node


def _cells_to_percentages(cells: tuple[int, ...] | list[int]) -> list[str]:
    """Convert a tuple of post-drag outer cell counts into percentage strings
    that sum to exactly 100%. Each value is at least `1%`. The last entry
    absorbs the rounding remainder so the sum is precise."""
    total = sum(cells)
    if total <= 0:
        return [f"{round(100 / max(1, len(cells)))}%" for _ in cells]
    raw = [max(1, round(c / total * 100)) for c in cells]
    raw[-1] += 100 - sum(raw)
    if raw[-1] < 1:
        raw[-1] = 1
    return [f"{n}%" for n in raw]


def _apply_resize(
    root_layout: dict,
    parent_path: tuple[int, ...],
    children_cells: tuple[int, ...],
) -> bool:
    """Renormalize the targeted parent container's children to percentages
    summing to 100, derived from `children_cells`. Mutates `root_layout` in
    place. Returns True iff the path resolved and the child counts match."""
    parent = _resolve_container(root_layout, parent_path)
    if parent is None:
        return False
    children = parent["children"]
    if len(children) != len(children_cells):
        return False
    for child, pct in zip(children, _cells_to_percentages(children_cells)):
        child["size"] = pct
    return True


def _normalize_layout_percentages(layout_dict: dict) -> bool:
    """Walk a LayoutSpec dict and, for every Container whose children all have
    percentage sizes, scale those percentages to sum to exactly 100%. Repairs
    workspaces saved by older Splitter code that produced sums < 100% (which
    showed up as a growing blank gap on the layout edge). Mutates in place;
    returns True if any container was rewritten."""
    changed = False

    def _walk(node: dict) -> None:
        nonlocal changed
        children = node.get("children") if isinstance(node, dict) else None
        if not isinstance(children, list):
            return
        sizes = [c.get("size") for c in children]
        if children and all(isinstance(s, str) and s.endswith("%") for s in sizes):
            try:
                nums = [float(s[:-1]) for s in sizes]  # type: ignore[union-attr]
            except ValueError:
                nums = []
            total = sum(nums) if nums else 0.0
            if nums and total > 0 and abs(total - 100) > 0.5:
                normalized = _cells_to_percentages(tuple(round(n * 1000) for n in nums))
                for c, pct in zip(children, normalized):
                    c["size"] = pct
                changed = True
        for c in children:
            _walk(c)

    _walk(layout_dict)
    return changed


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
        "FileEditor", FileEditor,
        description=(
            "Editable syntax-highlighted file editor. Pass `file_path` for "
            "an initial file. Pass `follow_selection: true` to subscribe to "
            "FileSelected events from a FileTree panel. Ctrl+S saves; the "
            "border title shows ' *' when there are unsaved changes. Prompts "
            "before discarding edits or overwriting external changes."
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
            "Editable scratch buffer; persists to <cwd>/.patchbai/scratch/<name>.md."
        ),
        props_schema={"name": str},
    )
    reg.register(
        "Terminal", Terminal,
        description=(
            "Real PTY in a panel. Use this for an interactive `claude` CLI "
            "session inside Patchbai — anything typed here is OPAQUE to the "
            "orchestrator (intentional escape-hatch behavior). Optional "
            "`command` (argv), `cwd`, and `env` props."
        ),
        props_schema={"command": list, "cwd": str, "env": dict},
    )
    reg.register(
        "SystemUsage", SystemUsage,
        description=(
            "Compact single-row CPU and RAM gauges with colored bars "
            "(green / yellow / red at 50% / 80% thresholds). Auto-refreshes "
            "every `interval` seconds. Uses `psutil` when available; "
            "otherwise shells out non-blockingly to `top` and `vm_stat` on "
            "macOS. `bar_width` controls the width of each progress bar."
        ),
        props_schema={"interval": float, "bar_width": int},
    )
    return reg


class PatchbaiApp(App):
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
        Binding("ctrl+shift+l", "open_theme_switcher", "themes"),
        Binding("ctrl+shift+r", "reset_panel_sizes", "reset panel sizes"),
        Binding("ctrl+shift+d", "open_change_cwd", "change cwd"),
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
        bypass_permissions: bool = False,
    ) -> None:
        super().__init__()
        # Cache for the currently-applied theme's extra_css. Initialized to
        # "" so save_theme can snapshot a clean state before any apply runs.
        self._active_theme_extra_css: str = ""
        self.cwd = Path(cwd) if cwd else Path.cwd()
        import asyncio as _asyncio
        self._cwd_swap_lock = _asyncio.Lock()
        self.event_bus = EventBus()
        self.activity_log = ActivityLog(self.event_bus)
        self._global_dir = Path(global_dir) if global_dir else global_config_dir()
        self.registry = registry or build_default_registry()
        self._local_widget_outcomes: list[LoadOutcome] = []
        # Load user-authored widgets from `~/.config/patchbai/widgets/` UNLESS
        # the caller passed in their own registry (test/embedded use case) OR
        # the widgets.local_dir_enabled flag is False.
        if registry is None:
            from patchbai.config import ConfigStore as _ConfigStore
            _cfg = _ConfigStore(global_dir=self._global_dir).load()
            if _cfg.widgets.local_dir_enabled:
                self._local_widget_outcomes = LocalWidgetLoader(
                    local_widgets_dir(global_dir=self._global_dir),
                    self.registry,
                ).load()
                _ok = sum(1 for o in self._local_widget_outcomes if o.status == "ok")
                _err = sum(1 for o in self._local_widget_outcomes if o.status != "ok")
                if _ok or _err:
                    log.info(
                        "loaded %d local widgets (%d errors) from %s",
                        _ok, _err,
                        local_widgets_dir(global_dir=self._global_dir),
                    )
        self._workspace: Workspace | None = None
        self._active_tab_id: str | None = None
        self._current_layout_name: str | None = None  # last `load_layout` name
        self._tab_focus_snapshots: dict[str, str] = {}  # tab_id -> last focused panel id
        # While _mount_workspace is building tabs, child widgets that auto-focus
        # on mount (Input, etc.) can drag the active TabPane around. Suppress
        # workspace-state updates from those activations so the saved ws.active
        # is what sticks once the dust settles. _mount_workspace clears this
        # and explicitly re-pins tc.active after a refresh tick.
        self._mounting_workspace: bool = False
        self.config_store = ConfigStore(global_dir=self._global_dir)
        self.layouts_store = NamedLayoutsStore(global_dir=self._global_dir)
        self.themes_store = NamedThemesStore(global_dir=self._global_dir)
        self.actions_registry = ActionRegistry()
        self._register_actions()

        # Permission posture: a PermissionGrants object is constructed iff
        # bypass is OFF (the default). Both the manager and the orchestrator
        # consume the same object so disk-backed rules apply uniformly.
        self._bypass_permissions = bypass_permissions
        self._permission_grants = (
            None if bypass_permissions else PermissionGrants(cwd=self.cwd)
        )
        # Latch: True while the PermissionModal is on the screen stack.
        # Prevents double-push; reset to False in the modal's dismiss callback.
        self._permission_modal_open = False
        # Unsub callable for the PermissionRequested handler; None when bypass mode.
        self._unsub_permission_requested: "callable | None" = None

        self.manager = manager or AgentManager(
            cwd=self.cwd,
            bus=self.event_bus,
            adapter_factory=RealSDKAdapter,
            permission_grants=self._permission_grants,
        )
        self.orchestrator = orchestrator or OrchestratorSession(
            cwd=self.cwd,
            bus=self.event_bus,
            manager=self.manager,
            apply_layout=self._orchestrator_apply_layout,
            layouts_store=self.layouts_store,
            themes_store=self.themes_store,
            config_store=self.config_store,
            actions=self.actions_registry,
            rebind_keys=self._rebind_keys,
            widget_registry=self.registry,
            current_layout=lambda: self._active_layout(),
            app=self,
            permission_grants=self._permission_grants,
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
        self.actions_registry.register(
            "open_theme_switcher", self.action_open_theme_switcher,
            description="Open the saved-themes switcher modal.", args_schema={},
        )
        self.actions_registry.register(
            "reset_panel_sizes", self.action_reset_panel_sizes,
            description=(
                "Discard mouse-drag panel size adjustments by reloading the "
                "active tab's layout from its named source."
            ),
            args_schema={},
        )
        self.actions_registry.register(
            "change_cwd",
            lambda path: self._dispatch_change_cwd(path),
            description="Change the workspace's cwd at runtime.",
            args_schema={"path": str},
        )
        self.actions_registry.register(
            "open_change_cwd", self.action_open_change_cwd,
            description="Open the change-cwd modal.", args_schema={},
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

    async def rename_tab(self, tab_id: str, title: str) -> dict:
        """Update the user-facing label of a tab. Returns a small result dict;
        never raises on bad input."""
        if self._workspace is None:
            return {"error": "workspace_not_initialized"}
        ws = self._workspace
        if all(t.id != tab_id for t in ws.tabs):
            return {"error": "unknown_tab_id", "tab_id": tab_id}
        if not isinstance(title, str) or not title.strip():
            return {"error": "title_must_be_nonempty_string"}
        new_ws = Workspace.model_validate({
            "version": ws.version,
            "tabs": [
                {**t.model_dump(mode="json"), "title": title}
                if t.id == tab_id else t.model_dump(mode="json")
                for t in ws.tabs
            ],
            "active": ws.active,
            "active_theme": ws.active_theme,
        })
        self._workspace = new_ws
        # Update the strip label without re-mounting the pane (preserves widget state).
        try:
            tc = self.query_one("#app-tabs", TabbedContent)
            tab = tc.get_tab(f"tab-{tab_id}")
            tab.label = title  # type: ignore[assignment]
        except Exception:
            # If the lookup fails (e.g., transient state), persistence still wins —
            # the next mount will reflect the new title.
            pass
        save_local_workspace(self.cwd, self._workspace)
        return {"renamed": tab_id, "title": title}

    async def reorder_tabs(self, tab_ids: list[str]) -> dict:
        """Rearrange tabs to match `tab_ids` order. Must be a permutation of
        the current tab ids — extras, missing ids, or duplicates are rejected.
        Preserves widget state by moving existing Tab/TabPane children in the
        live widget tree rather than rebuilding."""
        if self._workspace is None:
            return {"error": "workspace_not_initialized"}
        ws = self._workspace
        current_ids = [t.id for t in ws.tabs]
        if not isinstance(tab_ids, list) or not all(isinstance(x, str) for x in tab_ids):
            return {"error": "tab_ids_must_be_list_of_strings"}
        if sorted(tab_ids) != sorted(current_ids):
            return {
                "error": "tab_ids_not_a_permutation",
                "current_ids": current_ids,
                "given_ids": tab_ids,
            }
        # Already in order → no-op.
        if tab_ids == current_ids:
            return {"reordered": tab_ids, "noop": True}
        # Reorder workspace.
        by_id = {t.id: t for t in ws.tabs}
        new_ws = Workspace.model_validate({
            "version": ws.version,
            "tabs": [by_id[i].model_dump(mode="json") for i in tab_ids],
            "active": ws.active,
            "active_theme": ws.active_theme,
        })
        self._workspace = new_ws
        # Move existing TabPane and tab-strip Tab widgets into the new order.
        # Walk forward and place each item right after its predecessor — this
        # establishes the chain pane[0] -> pane[1] -> ... without ever asking
        # move_child to insert a widget before/after itself. The Tab widgets in
        # the strip live inside a `tabs-list` Horizontal nested under
        # ContentTabs, not directly under it — so we move each Tab through its
        # actual parent.
        try:
            from textual.widget import Widget as _Widget
            tc = self.query_one("#app-tabs", TabbedContent)
            from textual.widgets._content_switcher import ContentSwitcher
            switcher = tc.get_child_by_type(ContentSwitcher)
            panes = [tc.get_pane(f"tab-{tid}") for tid in tab_ids]
            tabs = [tc.get_tab(f"tab-{tid}") for tid in tab_ids]
            for i in range(len(tab_ids) - 1):
                switcher.move_child(panes[i + 1], after=panes[i])
                tab_parent = tabs[i].parent
                if isinstance(tab_parent, _Widget) and tabs[i + 1].parent is tab_parent:
                    tab_parent.move_child(tabs[i + 1], after=tabs[i])
        except Exception:
            # UI move failure leaves the in-memory workspace updated; persistence
            # below preserves the new order across restart.
            pass
        save_local_workspace(self.cwd, self._workspace)
        return {"reordered": tab_ids}

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
            "ctrl-shift-l themes · ctrl-shift-r reset panel sizes · "
            "ctrl-shift-d change cwd · "
            "ctrl-pgup/pgdn prev/next tab · ctrl-1..9 tab N · ctrl-t new tab · "
            "ctrl-w close tab · /reset new · /resume past · /rename title · "
            "/cd path · /help cmds · ? help",
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

    def action_open_theme_switcher(self) -> None:
        import asyncio as _asyncio

        try:
            builtins = sorted(
                n for n in self.available_themes.keys()
                if not n.startswith("patchbai:")
            )
        except Exception:
            builtins = []
        active = self.theme or ""
        if active.startswith("patchbai:"):
            active = active[len("patchbai:"):]

        def _on_picked(name: str | None) -> None:
            if not name:
                return
            _asyncio.create_task(self._apply_theme_by_name(name, persist=True))

        self.push_screen(
            ThemeSwitcherScreen(
                store=self.themes_store,
                available_builtins=builtins,
                active=active,
            ),
            _on_picked,
        )

    def _dispatch_change_cwd(self, path: str) -> None:
        """Action wrapper around change_cwd — schedules the async call."""
        import asyncio as _asyncio
        _asyncio.create_task(self._change_cwd_with_notify(path))

    async def _change_cwd_with_notify(self, path: str) -> None:
        result = await self.change_cwd(path)
        if "error" in result:
            err = result["error"]
            if err == "agents_running":
                names = ", ".join(a["name"] for a in result.get("agents", []))
                self.notify(
                    f"Refusing to change cwd: agents still running ({names}).",
                    severity="warning",
                )
            elif err == "invalid_path":
                self.notify(
                    f"Invalid path: {result.get('path') or result.get('detail')}",
                    severity="warning",
                )
            else:
                self.notify(f"change_cwd failed: {err}", severity="warning")
        elif "unchanged" in result:
            self.notify("cwd unchanged.")
        else:
            self.notify(f"cwd → {result['changed']}")

    def action_open_change_cwd(self) -> None:
        if self._workspace is None:
            return

        def _on_picked(path: str | None) -> None:
            if not path:
                return
            self._dispatch_change_cwd(path)

        from patchbai.widgets.change_cwd_screen import ChangeCwdScreen
        self.push_screen(
            ChangeCwdScreen(initial=str(self.cwd)),
            _on_picked,
        )

    async def _apply_theme_by_name(
        self, name: str, *, persist: bool = False, scope: str = "global",
    ) -> None:
        """Single seam used by boot, the modal, and the load_theme tool path."""
        spec = self.themes_store.load(name)
        if spec is not None:
            await apply_theme(self, spec, theme_name=name)
        else:
            try:
                if name not in self.available_themes:
                    return
            except Exception:
                return
            if _EXTRA_CSS_KEY in self.stylesheet.source:
                del self.stylesheet.source[_EXTRA_CSS_KEY]
            self._active_theme_extra_css = ""
            self.theme = name
            try:
                self.refresh_css()
            except Exception:
                pass
        if not persist:
            return
        if scope == "global":
            cfg = self.config_store.load()
            cfg.ui.active_theme = name
            self.config_store.save(cfg)
        elif scope == "project" and self._workspace is not None:
            ws = self._workspace.model_copy(update={"active_theme": name})
            self._workspace = ws
            from patchbai.persistence.workspace_store import save_workspace
            save_workspace(self.cwd, ws)

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
            return self._migrate_workspace_percentages(ws)
        # Migration: legacy layout.json -> single-tab workspace.
        from patchbai.persistence.layout_store import load_layout as _load_legacy
        legacy = _load_legacy(self.cwd)
        if legacy is not None:
            return workspace_from_layout(legacy, tab_id="default", title="default")
        return workspace_from_layout(dashboard_layout(), tab_id="default", title="default")

    def _migrate_workspace_percentages(self, ws: Workspace) -> Workspace:
        """One-shot repair: walk every tab's layout and renormalize Container
        percentage children to sum to 100%. Repairs workspaces saved by older
        Splitter code whose drift left visible blank space at the layout
        edges. No-op when sums are already at 100%."""
        raw = ws.model_dump(mode="json")
        any_changed = False
        for tab in raw["tabs"]:
            if _normalize_layout_percentages(tab["layout"]["layout"]):
                any_changed = True
        if not any_changed:
            return ws
        try:
            migrated = Workspace.model_validate(raw)
        except Exception:
            return ws
        # Persist the repaired layout so the next launch starts clean.
        try:
            save_local_workspace(self.cwd, migrated)
        except Exception:
            pass
        return migrated

    async def _mount_workspace(self, ws: Workspace) -> None:
        tc = self.query_one("#app-tabs", TabbedContent)
        self._mounting_workspace = True
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
        # The active tab is applied first (with focus) and pinned via tc.active;
        # non-active tabs get their layout built without their `focus` directive
        # honored, since focusing a widget in a hidden TabPane makes Textual
        # switch to it (a Terminal tab with focus="term" or any auto-focusing
        # child like an Input will otherwise steal activation on launch). Each
        # non-active layout is followed by a re-pin: widgets like Input
        # auto-focus on mount independent of `apply_focus` and can still drag
        # the active tab over. Pinning per-iteration keeps ws.active sticky.
        target_active = f"tab-{ws.active}"
        active_tab = next((t for t in ws.tabs if t.id == ws.active), None)
        if active_tab is not None:
            area = self.query_one(f"#panel-area-{active_tab.id}", Container)
            try:
                await apply_layout(
                    area, active_tab.layout, self.registry, layout_name=None,
                    apply_focus=True,
                )
            except Exception:
                pass
            tc.active = target_active
        for t in ws.tabs:
            if t.id == ws.active:
                continue
            area = self.query_one(f"#panel-area-{t.id}", Container)
            try:
                await apply_layout(
                    area, t.layout, self.registry, layout_name=None,
                    apply_focus=False,
                )
            except Exception:
                pass
            if tc.active != target_active:
                tc.active = target_active
        tc.active = target_active

        # Final re-pin: child widgets (Input, etc.) that auto-focus on mount
        # may queue TabActivated messages that fire after this function returns.
        # Schedule a deferred pin once Textual has flushed those messages, then
        # clear the suppression flag so the canonical activation handler runs
        # for the user's first real tab switch.
        def _finalize_active() -> None:
            try:
                if tc.active != target_active:
                    tc.active = target_active
            finally:
                self._mounting_workspace = False
                self._active_tab_id = ws.active

        self.call_after_refresh(_finalize_active)

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

    async def change_cwd(self, new_cwd: "str | Path") -> dict:
        """Re-root the running workspace at `new_cwd`. Stops the
        orchestrator and manager, swaps `self.cwd`, rebuilds both, loads
        (or seeds) the new cwd's workspace, re-applies the active theme,
        and publishes WorkspaceCwdChanged.

        Returns a result dict; never raises on user input.
        """
        from patchbai.events import WorkspaceCwdChanged
        from patchbai.agents.sdk_adapter import RealSDKAdapter

        async with self._cwd_swap_lock:
            # Validate.
            try:
                resolved = Path(new_cwd).expanduser().resolve()
            except Exception as e:
                return {"error": "invalid_path", "detail": str(e)}
            if not resolved.exists() or not resolved.is_dir():
                return {"error": "invalid_path", "path": str(resolved)}
            try:
                current = Path(self.cwd).resolve()
            except Exception:
                current = self.cwd
            if resolved == current:
                return {"unchanged": True}

            # Refuse with running children.
            running = [
                {"id": info.id, "name": info.name}
                for info in self.manager.list_infos()
                if not info.state.is_terminal
            ]
            if running:
                return {"error": "agents_running", "agents": running}

            # Save the OLD workspace one last time.
            if self._workspace is not None:
                try:
                    save_local_workspace(self.cwd, self._workspace)
                except Exception:
                    pass

            # Tear down current orchestrator + manager.
            try:
                await self.orchestrator.stop()
            except Exception:
                pass
            try:
                await self.manager.shutdown()
            except Exception:
                pass

            # Swap cwd and reset workspace state.
            self.cwd = resolved
            self._workspace = None
            self._active_tab_id = None
            self._current_layout_name = None
            self._tab_focus_snapshots.clear()

            # Rebuild manager + orchestrator.
            self._permission_grants = (
                None if self._bypass_permissions else PermissionGrants(cwd=self.cwd)
            )
            self.manager = AgentManager(
                cwd=self.cwd, bus=self.event_bus,
                adapter_factory=RealSDKAdapter,
                permission_grants=self._permission_grants,
            )
            self.orchestrator = OrchestratorSession(
                cwd=self.cwd, bus=self.event_bus, manager=self.manager,
                apply_layout=self._orchestrator_apply_layout,
                layouts_store=self.layouts_store,
                themes_store=self.themes_store,
                config_store=self.config_store,
                actions=self.actions_registry,
                rebind_keys=self._rebind_keys,
                widget_registry=self.registry,
                current_layout=lambda: self._active_layout(),
                app=self,
                permission_grants=self._permission_grants,
            )
            self.orchestrator._auto_title_enabled = True
            await self.orchestrator.start()

            # Load (or seed) the new workspace.
            ws = self._load_or_seed_workspace()
            self._workspace = ws
            self._active_tab_id = ws.active
            await self._mount_workspace(ws)
            save_local_workspace(self.cwd, ws)

            # Re-apply theme.
            active_name = (
                ws.active_theme
                or self.config_store.load().ui.active_theme
                or "default"
            )
            try:
                await self._apply_theme_by_name(active_name, persist=False)
            except Exception:
                try:
                    await self._apply_theme_by_name("default", persist=False)
                except Exception:
                    pass

            self.event_bus.publish(WorkspaceCwdChanged(cwd=str(self.cwd)))
            return {"changed": str(self.cwd)}

    async def set_bypass_permissions(self, bypass: bool) -> dict:
        """Flip permission mode at runtime. Tears down + rebuilds the orchestrator
        and manager. Returns a result dict mirroring change_cwd's shape:
        - {"changed": "bypass"} or {"changed": "require"} on success.
        - {"unchanged": True} if already in the requested mode.
        - {"error": "agents_running", "agents": [...]} if children are alive.
        """
        from patchbai.agents.sdk_adapter import RealSDKAdapter

        async with self._cwd_swap_lock:
            # Already in the requested mode.
            if self._bypass_permissions == bypass:
                return {"unchanged": True}

            # Refuse with running children.
            running = [
                {"id": info.id, "name": info.name}
                for info in self.manager.list_infos()
                if not info.state.is_terminal
            ]
            if running:
                return {"error": "agents_running", "agents": running}

            # Tear down current orchestrator + manager.
            try:
                await self.orchestrator.stop()
            except Exception:
                pass
            try:
                await self.manager.shutdown()
            except Exception:
                pass

            # Flip mode and rebuild grants.
            self._bypass_permissions = bypass
            self._permission_grants = (
                None if bypass else PermissionGrants(cwd=self.cwd)
            )

            # Manage the PermissionRequested subscription dynamically.
            if bypass:
                # Switching to bypass: drop the subscription if present.
                if self._unsub_permission_requested is not None:
                    self._unsub_permission_requested()
                    self._unsub_permission_requested = None
            else:
                # Switching to require: add the subscription if not present.
                if self._unsub_permission_requested is None:
                    self._unsub_permission_requested = self.event_bus.subscribe(
                        PermissionRequested, self._on_permission_requested,
                    )

            # Rebuild manager + orchestrator with the new grants.
            self.manager = AgentManager(
                cwd=self.cwd, bus=self.event_bus,
                adapter_factory=RealSDKAdapter,
                permission_grants=self._permission_grants,
            )
            self.orchestrator = OrchestratorSession(
                cwd=self.cwd, bus=self.event_bus, manager=self.manager,
                apply_layout=self._orchestrator_apply_layout,
                layouts_store=self.layouts_store,
                themes_store=self.themes_store,
                config_store=self.config_store,
                actions=self.actions_registry,
                rebind_keys=self._rebind_keys,
                widget_registry=self.registry,
                current_layout=lambda: self._active_layout(),
                app=self,
                permission_grants=self._permission_grants,
            )
            self.orchestrator._auto_title_enabled = True
            await self.orchestrator.start()

            return {"changed": "bypass" if bypass else "require"}

    # --- stats aggregation -------------------------------------------------

    def _on_stats_changed(self, _event) -> None:
        """Re-aggregate token / cost / active-agent counters across the
        orchestrator and every child agent, and publish a StatsUpdated event
        so the StatusBar repaints. Fired by AgentTokensTouched (every
        ResultMessage), AgentSpawned, and AgentStateChanged."""
        tokens_in = 0
        tokens_out = 0
        cost = 0.0
        try:
            orch_info = self.orchestrator.info
            tokens_in += orch_info.tokens_in
            tokens_out += orch_info.tokens_out
            cost += orch_info.cost
        except Exception:
            pass
        active = 0
        try:
            for info in self.manager.list_infos():
                tokens_in += info.tokens_in
                tokens_out += info.tokens_out
                cost += info.cost
                if not info.state.is_terminal:
                    active += 1
        except Exception:
            pass
        self.event_bus.publish(StatsUpdated(
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost=cost,
            active_agents=active,
        ))

    def _on_permission_requested(self, event: PermissionRequested) -> None:
        from patchbai.widgets.permission_modal import PermissionModal
        # Defensive: grants may have just been swapped to None by
        # set_bypass_permissions before this in-flight event lands.
        if self._permission_grants is None:
            return
        # The modal's own subscription handles queueing once it's mounted.
        # Skip pushing a second one if it's already on the stack.
        if self._permission_modal_open:
            return
        self._permission_modal_open = True

        def _on_dismissed(_: object) -> None:
            self._permission_modal_open = False

        # Lookup is unified across orchestrator + child agents.
        def _inbox_lookup(agent_id: str):
            if agent_id == "orchestrator":
                return self.orchestrator.get_permission_inbox()
            return self.manager.get_permission_inbox(agent_id)

        self.push_screen(
            PermissionModal(
                inbox_lookup=_inbox_lookup,
                grants=self._permission_grants,
                initial_request=event,
            ),
            _on_dismissed,
        )

    # --- splitter persistence ---------------------------------------------

    def _on_layout_resized(self, event: LayoutResized) -> None:
        """Persist the new sizes from a Splitter drag back to the workspace.
        Mutates only the size fields of the targeted children — does not
        re-apply the layout, so the live widget tree (with the inline cell
        sizes the splitter just set) stays as the user left it."""
        if self._workspace is None:
            return
        # Find the tab and walk its layout to the parent container.
        new_tabs: list[dict] = []
        mutated = False
        for t in self._workspace.tabs:
            tab_dump = t.model_dump(mode="json")
            if t.id == event.tab_id:
                root_layout = tab_dump["layout"]["layout"]
                if _apply_resize(root_layout, event.parent_path, event.children_cells):
                    mutated = True
            new_tabs.append(tab_dump)
        if not mutated:
            return
        try:
            candidate = Workspace.model_validate({
                "version": self._workspace.version,
                "tabs": new_tabs,
                "active": self._workspace.active,
                "active_theme": self._workspace.active_theme,
            })
        except Exception:
            # Validation failure leaves both memory and disk untouched.
            return
        self._workspace = candidate
        save_local_workspace(self.cwd, candidate)

    async def action_reset_panel_sizes(self) -> None:
        """Discard any drag-set sizes by reloading the active tab's layout
        from its named source (or the built-in dashboard if unnamed)."""
        if self._active_tab_id is None:
            return
        spec: LayoutSpec | None = None
        if self._current_layout_name:
            spec = self.layouts_store.load(self._current_layout_name)
        if spec is None:
            spec = dashboard_layout()
        await self._apply_to_tab(
            self._active_tab_id, spec, layout_name=self._current_layout_name,
        )

    # --- tab activation handler --------------------------------------------

    def on_tabbed_content_tab_activated(
        self, event: TabbedContent.TabActivated,
    ) -> None:
        """Triggered by TabbedContent when the user (or code) switches the active
        pane. Updates workspace state, persists, fires our TabSwitched event, and
        restores focus to the tab's last-focused panel id."""
        if self._workspace is None:
            return
        # While the workspace is being mounted, child widgets that auto-focus
        # on mount (Input, FocusableTextArea, etc.) can fire TabActivated for
        # panes that aren't the saved active tab. Ignore those — _mount_workspace
        # explicitly pins ws.active once mount settles via call_after_refresh.
        if self._mounting_workspace:
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
        self.event_bus.subscribe(LayoutResized, self._on_layout_resized)
        self.event_bus.subscribe(AgentTokensTouched, self._on_stats_changed)
        self.event_bus.subscribe(AgentStateChanged, self._on_stats_changed)
        self.event_bus.subscribe(AgentSpawned, self._on_stats_changed)
        if self._permission_grants is not None:
            self._unsub_permission_requested = self.event_bus.subscribe(
                PermissionRequested, self._on_permission_requested,
            )
        ws = self._load_or_seed_workspace()
        self._workspace = ws
        self._active_tab_id = ws.active
        await self._mount_workspace(ws)
        save_local_workspace(self.cwd, ws)

        # Theme seed: snapshot the current Textual theme as "default" if not present.
        if self.themes_store.load("default") is None:
            try:
                pal = palette_from_textual_theme(self.current_theme)
                self.themes_store.save(
                    "default", ThemeSpec(palette=pal, extra_css=""),
                )
            except Exception:
                # Snapshot may fail if Textual's theme objects shape ever
                # changes — boot must not abort.
                pass

        # Resolve active theme: workspace.active_theme → config.ui.active_theme → "default".
        active_name = (
            ws.active_theme
            or self.config_store.load().ui.active_theme
            or "default"
        )
        try:
            await self._apply_theme_by_name(active_name, persist=False)
        except Exception:
            # Bad active theme must not brick boot. Fall back to default.
            try:
                await self._apply_theme_by_name("default", persist=False)
            except Exception:
                pass  # last-resort: leave Textual default in place.

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
        # Only treat the row as an agent_id if the originating DataTable lives
        # inside the built-in AgentTable. Without this guard, ANY user-authored
        # widget that owns a DataTable (e.g. MulticaIssues, whose row keys are
        # issue identifiers like "BUO-597") would have its RowSelected message
        # bubble here and push a TranscriptScreen modal with a bogus agent_id.
        # That modal then sits on top of the screen stack and intercepts every
        # subsequent tab-strip click, which is the visible "I can't click back
        # into the Agents tab" symptom users hit after visiting Multica.
        table = event.data_table
        node = table.parent
        is_agent_row = False
        while node is not None:
            if isinstance(node, AgentTable):
                is_agent_row = True
                break
            node = node.parent
        if not is_agent_row:
            return
        agent_id = str(event.row_key.value)
        await self.push_screen(TranscriptScreen(agent_id=agent_id, event_bus=self.event_bus))
