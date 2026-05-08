# App-level and Panel-level Tabs — Design

## Goal

Let the orchestrator agent (and the user via hotkeys) organize the workspace
into multiple persistent tabs at two levels:

- **App-level tabs** — top-of-screen tab strip, each tab is its own layout.
  Switching tabs preserves widget state (Terminal PTY, FileTree expansion,
  scroll positions).
- **Panel-level tabs** — a node inside a layout that holds multiple widgets
  reachable via a per-panel tab strip.

Two motivating user phrases:

> "Add a new tab with a file tree and file viewer."
> "Add a new tab to the activity feed that shows a log tail of the SQL
> container's logs."

The first creates an app-level tab seeded with a two-panel layout. The second
mutates the layout in place to convert the activity-feed slot into a `Tabs`
node holding both the original `ActivityFeed` and a new `LogTail`.

## Constraints from earlier brainstorming decisions

- **Persistence semantics:** all tabs stay mounted. Switching tabs only
  shows/hides; widget state survives.
- **Tab → layout coupling:** each tab owns its own `LayoutSpec`,
  independently mutable. Named layouts (`NamedLayoutsStore`) remain a
  separate concept — templates a tab can be seeded from.
- **OrchestratorChat:** per-tab optional. A workspace must contain at least
  one `OrchestratorChat` across all tabs combined; an individual tab may
  contain zero or one.
- **Panel-tab content:** leaf-only. Each panel-tab pane holds exactly one
  widget. Splits inside a single panel-tab are not supported.
- **Tool surface:** discrete tab tools (`add_tab`, `close_tab`, `switch_tab`,
  `list_tabs`) plus active-tab-aware `set_layout` / `get_layout`.
- **Workspace persistence:** the full workspace (tab list + per-tab layouts +
  active-tab-id) is saved to `<cwd>/.patchfeld/workspace.json` on every change
  and restored on launch.
- **Tab strip placement:** between `CommandBar` and the panel area.
- **Rendering:** lean on Textual's built-in `TabbedContent` for both levels.

## Architecture

### Data model

Two additions in `patchfeld/layout/spec.py` and one new file
`patchfeld/workspace/spec.py`.

**Panel-level tabs** — new `Tabs` variant in the layout-node union:

```python
class Tabs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["tabs"]
    size: str | None = None
    children: list[Panel] = Field(min_length=1)
    active: str | None = None   # panel id of initial pane; defaults to children[0].id
```

`Node` becomes `Container | Panel | Tabs`. The current spec relies on
`extra="forbid"` + Pydantic smart-union ordering to disambiguate
`Container` from `Panel`. Adding `Tabs` (which shares `Container`'s `type`
+ `children` shape) makes that ordering fragile, so we promote the union
to a discriminated one keyed off `type`:

- `Container.type` is `Literal["horizontal", "vertical"]`.
- `Tabs.type` is `Literal["tabs"]`.
- `Panel` has no `type` field; it's the default branch when `type` is
  absent.

Encoded as a `Union[Annotated[Container | Tabs, Field(discriminator="type")], Panel]`,
with `Panel` tried last. This makes the disambiguation explicit and
removes the existing "load-bearing on extra=forbid" comment in
`spec.py`.

`LayoutSpec`'s validator weakens:

- Old: "exactly one panel with `widget='OrchestratorChat'`."
- New: "**at most** one panel with `widget='OrchestratorChat'`."

The "at least one" half of the invariant moves up to `Workspace`.

**App-level tabs** — new file `patchfeld/workspace/spec.py`:

```python
class Tab(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str          # stable; used by switch_tab/close_tab
    title: str       # user-facing label on the tab strip
    layout: LayoutSpec

class Workspace(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = 1
    tabs: list[Tab] = Field(min_length=1)
    active: str      # id of currently active tab

    @model_validator(mode="after")
    def _at_least_one_chat(self) -> "Workspace":
        if not any(_contains_chat(t.layout.layout) for t in self.tabs):
            raise ValueError("workspace must contain at least one OrchestratorChat")
        if self.active not in {t.id for t in self.tabs}:
            raise ValueError(f"active tab id '{self.active}' not in tabs")
        return self
```

`_contains_chat` walks a `Node` recursively (including the new `Tabs` node)
and returns `True` if any `Panel` has `widget == "OrchestratorChat"`.

### Persistence

New module `patchfeld/persistence/workspace_store.py`, modeled on the existing
`layout_store.py`:

```python
def load_workspace(cwd: Path) -> Workspace | None: ...
def save_workspace(cwd: Path, ws: Workspace) -> None: ...
```

- Path: `<cwd>/.patchfeld/workspace.json`.
- Atomic write (temp file + `os.replace`), same pattern as today.
- Save is called from a single internal `PatchfeldApp._save_workspace()` method
  invoked from every mutation path (orchestrator tools and hotkey actions).

**Migration on launch** (in `PatchfeldApp.on_mount`):

1. If `workspace.json` exists → `load_workspace`. Done.
2. Else if legacy `layout.json` exists → wrap its `LayoutSpec` in
   `Workspace(tabs=[Tab(id="default", title="default", layout=...)],
   active="default")`, write `workspace.json`. Leave `layout.json` in place
   for one release as a safety net; do not write to it going forward.
3. Else → seed from `dashboard_layout()` as a single-tab workspace and save.

`NamedLayoutsStore` is unchanged. `add_tab(layout=...)` accepts either an
inline `LayoutSpec` dict or the *name* of a saved layout; resolution to a
`LayoutSpec` happens in the tool handler before the workspace mutation.

### Composition

`PatchfeldApp.compose` becomes:

```
CommandBar
TabbedContent#app-tabs                  (one TabPane per Workspace.tab)
  TabPane id=tab.id title=tab.title
    Container id=f"panel-area-{tab.id}"
StatusBar
```

The existing engine `apply_layout(container, spec, registry, *, layout_name=...)`
is unchanged. We just call it once per tab, against that tab's
`panel-area-<tab.id>` container. The engine's `_last_applied_spec` is a
`WeakKeyDictionary` keyed by container, so per-tab caches work without
modification.

**Up-front mount.** Textual's `TabbedContent` lazy-mounts panes on first
activation. To honor "persistent — keep mounted from launch," `on_mount`
iterates every tab and calls `apply_layout` immediately, regardless of
which is active. A `Notebook` in tab 3 has its scratch buffer alive from
launch, not from first activation.

**Tab activation.** We subscribe to `TabbedContent.TabActivated`. The
handler:

1. Updates `Workspace.active`.
2. Saves the workspace.
3. Publishes `TabSwitched(tab_id, title)` on the bus (and a
   `LayoutApplied(spec=tab.layout, layout_name=..., tab_id=tab.id)` so the
   `StatusBar`'s layout-name display refreshes).
4. Restores focus to the tab's `spec.focus` panel id, falling back to the
   tab's last-focused panel id (small per-tab snapshot dict).

### Engine changes for the `Tabs` node

`patchfeld/layout/engine.py` gets one extra branch in `_build`:

```python
elif isinstance(node, Tabs):
    panes = []
    for child in node.children:
        widget = _build(child, registry)        # reuses the Panel branch
        panes.append(TabPane(child.title or _default_title(child),
                             widget,
                             id=f"tabpane-{child.id}"))
    tc = TabbedContent(*panes,
                       initial=f"tabpane-{node.active or node.children[0].id}")
    if node.size:
        tc.styles.width = node.size   # same as the Container branch
    return tc
```

`_collect_panels` extends to descend into `Tabs.children`, so `diff`
naturally reuses widgets across `set_layout` calls when (id, widget) match
inside a panel-tab — same UX guarantee as today's `Container`-only diff.

Border-title resolution applies per `Panel` exactly as today; the
`TabPane`'s own tab strip provides the panel-tab labels.

### MCP tools

New file `patchfeld/orchestrator/tabs_tools.py`:

- `add_tab(title: str, layout: dict | str | None = None, activate: bool = True)`
  Creates a new tab. `layout` may be an inline `LayoutSpec` dict, the
  *name* of a saved layout (resolved via `NamedLayoutsStore`), or `None`
  (seeded with a single `OrchestratorChat` panel, or an empty
  `ActivityFeed`-only layout if the workspace already has chat elsewhere).
  Returns `{tab_id, title}`. If `activate`, makes it the active tab.

- `close_tab(tab_id: str)`
  Closes the tab. Refuses if it would leave zero `OrchestratorChat` panels
  across the workspace (returns a structured error: `{error:
  "would_leave_no_chat", suggestion: "add chat to another tab first"}`).
  Refuses if it's the last tab. Active tab falls back to the tab to the
  left.

- `switch_tab(tab_id: str)`
  Activates the tab. Returns the active tab's effective layout (reusing
  `get_layout`'s formatter so the agent sees what's now on screen).

- `list_tabs()`
  Returns `[{id, title, active, has_chat, panel_ids}]` per tab.

Revisions to existing tools in `patchfeld/orchestrator/tools.py`:

- `set_layout` / `get_layout` operate on the active tab implicitly. Optional
  `tab_id` arg overrides target. Responses gain `tab_id` and `tab_title`.
- `save_layout` accepts optional `tab_id` (defaults to active).
- `load_layout(name)` loads a named layout into the active tab (replacing
  its spec). Optional `tab_id` to target a different one. Optional
  `as_new_tab: bool = False` to instead create a new tab seeded from the
  named layout — sugar over `add_tab(layout=name)`.

Tool descriptions are updated so the agent knows tabs exist. `set_layout`'s
description gains: "Edits the **active** tab's layout. To create a new tab,
use `add_tab`. To target a different tab, pass `tab_id`." `get_layout` and
`set_layout` schemas document the new `Tabs` node type with an example.

### Keybindings

Added to `PatchfeldApp.BINDINGS`:

- `ctrl+pageup` / `ctrl+pagedown` — previous / next tab (Textual's
  `TabbedContent` defaults; we surface them in `?` help).
- `ctrl+t` — new tab. Opens a small modal asking for a title; on submit,
  creates a tab seeded with a default `OrchestratorChat`-only layout (or an
  empty `ActivityFeed`-only layout if chat already exists elsewhere).
- `ctrl+w` — close active tab. No-op (with notify) if last tab, or if
  closing would leave zero chats.
- `ctrl+1` … `ctrl+9` — switch by tab index.

All four routes — MCP tool, `ctrl+t` modal, `ctrl+w`, `ctrl+pgup/pgdn` —
hit the same `PatchfeldApp._mutate_workspace(...)` internal method so
persistence and event publishing live in one place.

### Events

Additions to `patchfeld/events.py`:

```python
@dataclass(frozen=True)
class TabAdded:    tab_id: str; title: str
@dataclass(frozen=True)
class TabClosed:   tab_id: str
@dataclass(frozen=True)
class TabSwitched: tab_id: str; title: str
```

Existing `LayoutApplied` and `LayoutFailed` gain an optional
`tab_id: str | None` field so subscribers can route per-tab.

### Error handling

- **Validation:** Pydantic errors on `Workspace` / `LayoutSpec` / `Tabs`
  surface to MCP tools as structured errors with field paths, same pattern
  as today's `set_layout`.
- **Hotkey actions** never raise. Failures `notify(...)` and leave state
  untouched.
- **Apply failures:** `apply_layout` already builds atomically and publishes
  `LayoutFailed` on `_build` errors. We extend the event with the offending
  `tab_id` so the user sees which tab failed.

## Testing strategy

Three layers, mirroring how layout is tested today.

**1. Unit — spec validation**

- `Tabs` node round-trips through Pydantic.
- `Tabs` rejects empty `children`.
- `LayoutSpec` accepts zero `OrchestratorChat` (was rejected before).
- `LayoutSpec` still rejects two `OrchestratorChat` panels.
- `Workspace` rejects a tab list where no tab contains chat.
- `Workspace` rejects an `active` id not in `tabs`.

**2. Engine — `Tabs` build & diff**

- `_build` on a `Tabs` node produces a `TabbedContent` with one `TabPane`
  per child.
- `node.active` selects the right initial pane.
- `diff` reuses widgets across `set_layout` when (id, widget) match inside
  a `Tabs` node.
- `apply_layout` against a per-tab container is idempotent (same fast-path
  guarantee as today).

**3. App — Pilot tests**

- Migration: legacy `layout.json` on disk produces a one-tab workspace on
  launch.
- `add_tab` MCP round-trip: tool call creates a `TabPane`, saves
  `workspace.json`, fires `TabAdded`.
- `close_tab` round-trip: refuses to close the last tab; refuses to close a
  tab that would leave zero chats; otherwise removes the pane.
- `switch_tab` round-trip: changes active, fires `TabSwitched`, restores
  focus.
- **Persistence semantics:** mount a workspace with two tabs, where tab 2
  contains a `Notebook`. Type into the `Notebook`, switch to tab 1, switch
  back — buffer survives. Then assert via DOM query that tab 2's widgets
  were mounted before its first activation (proves the up-front-mount
  step).
- Hotkeys: `ctrl+t` opens the modal; `ctrl+w` closes; `ctrl+1`/`ctrl+2`
  switch.

## Out of scope

- Tab drag-to-reorder, tab close buttons, mid-click close — power-user
  affordances for a follow-up.
- Per-tab orchestrator sessions. There remains exactly one orchestrator
  session per app; multiple `OrchestratorChat` widgets are views onto it
  (input/scroll diverge across views).
- Cross-tab "send this panel to tab N" or panel drag-and-drop — out of
  scope; the only spec-edit API is `set_layout`.
- Tab pinning / opt-in persistence (a brainstorming option that was
  declined).
- Removal of the legacy `layout.json` read path — kept as a safety net for
  one release; deletion is a follow-up.

## Open questions for implementation

- Default seed when `add_tab(layout=None)` is called and the workspace
  already has chat: chat-only or `ActivityFeed`-only? Probably the latter
  — most "add a new tab" requests will follow with a `set_layout` anyway.
- How aggressive to be on `workspace.json` saves: every mutation, or
  debounced? Probably every mutation — small files, atomic writes, mirrors
  today's `layout.json` behavior.
