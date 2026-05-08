# Panel Border Titles

## Goal

Every mounted panel renders a human-readable title in its top border, so:

1. Users immediately see what each panel is.
2. Users can refer to panels by title in chat ("make the Activity Panel 2x its size") and the orchestrator can resolve the reference back to a panel `id`.
3. The orchestrator can override any panel's title when shipping a `LayoutSpec`.

## Non-goals

- Replacing `Panel.id` as the canonical identifier. Titles are descriptive metadata only; ids stay the addressing primitive for `set_layout`, focus, and persistence.
- Title-aware shortcut tools (`set_panel_title`, `set_panel_size`). Out of scope; the agent reads `get_layout`, mutates the spec, and ships it back via `set_layout`. Reconsider if round-tripping the full spec proves clumsy in practice.
- Fine-grained, in-place title updates. `apply()` already remounts the container atomically on any spec change today, so a title-only edit will remount and lose transient widget state. That's pre-existing behavior — not introduced here. Track as a follow-up.
- `border_subtitle`. Could be useful for showing widget state ("running" / "idle"), but not part of this change.

## Design

### 1. Schema: `Panel.title`

Add one optional field to `Panel` in `patchbai/layout/spec.py`:

```python
class Panel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    widget: str
    props: dict = Field(default_factory=dict)
    size: str | None = None
    title: str | None = None   # NEW
```

Plain text. Optional. `extra="forbid"` stays. Existing saved layouts (`~/.config/patchbai/layouts/*.json`, `<cwd>/.patchbai/layout.json`) parse cleanly without migration because the field is additive and defaults to `None`.

### 2. Per-widget defaults

Convention: any widget class in the registry can declare a default border title in one of two ways. The engine prefers (a) when present:

a. **Classmethod** — `default_border_title(cls, props: dict) -> str`. Used when the title depends on construction props (e.g., `FileTree(path=...)`). Receives the panel's `props` dict, returns a string.
b. **Class attribute** — `DEFAULT_BORDER_TITLE: str`. Used for widgets whose title never varies.

A single classmethod (rather than an instance method) keeps the resolver callable from both the engine path (where a live widget exists) and the `get_layout` path (where we only have the dumped `LayoutSpec`). Avoids drift between "title shown on screen" and "title returned to the agent".

Concrete defaults across the in-tree widgets:

| Widget | Default title |
|---|---|
| `OrchestratorChat` | `"Orchestrator"` |
| `AgentTable` | `"Agents"` |
| `AgentTranscript` | `f"Agent: {agent_id}"` (raw id from props; resolved name is a follow-up) |
| `RichTranscript` | `f"Transcript: {agent_id}"` |
| `ActivityFeed` | `"Activity"` |
| `Markdown` | `"Markdown"`, or `f"Markdown: {basename(file_path)}"` if `file_path` set |
| `FileViewer` | `"File"`, or `f"File: {basename(file_path)}"` if `file_path` set |
| `FileTree` | `"Files"`, or `f"Files: {compact(path)}"` if `path` set |
| `DiffViewer` | `"Diff"` |
| `LogTail` | `"Log"`, or `f"Log: {basename(file_path)}"` if `file_path` set |
| `Notebook` | `"Note"`, or `f"Note: {name}"` if `name` set |
| `Terminal` | `"Terminal"`, or `f"Terminal: {basename(command[0])}"` if `command` set |

Resolution order, factored into a single helper `resolve_title(panel: Panel, widget_cls: type[Widget]) -> str`:

1. `panel.title` if set — explicit override wins.
2. `widget_cls.default_border_title(panel.props)` if defined.
3. `widget_cls.DEFAULT_BORDER_TITLE` if set.
4. `widget_cls.__name__` as a last-resort fallback.

The helper takes the widget *class* (not an instance) and the panel's `props`, so it is identical when called from the engine (with `registry.get(node.widget)`) and from `get_layout` (walking a dumped spec dict). Same input → same output, no drift between on-screen and agent-visible titles.

### 3. Engine + per-widget borders

#### Engine changes (`patchbai/layout/engine.py`, `_build`)

After constructing the widget and assigning `widget.id = f"panel-{node.id}"`:

1. **Border safety net.** If the widget has no border style set (`widget.styles.has_rule("border")` returns False), apply `widget.styles.border = ("round", "$surface-lighten-2")`. Widgets that already define a border keep theirs.
2. **Title resolution.** Compute `title = resolve_title(node, type(widget))` and assign `widget.border_title = title`. Wrap the resolution in `try/except`: if a buggy `default_border_title` on a custom widget raises, fall back to the class name and continue. A bad title must never abort the apply.

#### Per-widget DEFAULT_CSS additions

Add `border: round $surface-lighten-2` to DEFAULT_CSS of:

- `Markdown` (`patchbai/widgets/markdown.py`)
- `FileTree` (`patchbai/widgets/file_tree.py`)
- `Notebook` (`patchbai/widgets/notebook.py`)
- `FileViewer` (`patchbai/widgets/file_viewer.py`)
- `RichTranscript` (`patchbai/widgets/rich_transcript.py`) — outer container; the inner `border-left` styling on individual messages is unaffected.

These give each widget explicit control of its border style. The engine's safety net then exists purely as the fallback for unknown / orchestrator-supplied custom widgets.

### 4. `get_layout` MCP tool

#### Wiring

`PatchbaiApp._apply` already stores the most recent spec on `self._current_spec`. Expose it to the orchestrator session by adding a new keyword-only parameter to `OrchestratorSession.__init__`:

```python
current_layout: Callable[[], LayoutSpec | None] | None = None
```

`PatchbaiApp.__init__` passes `current_layout=lambda: self._current_spec` when constructing the session, symmetric to the existing `apply_layout` injection.

The session forwards `current_layout` into `build_orchestrator_mcp_server` and `build_orchestrator_tools`, which build a `get_layout` handler when the callable is provided.

#### Handler behavior

```python
def _get_layout_handler(current_layout, widget_registry):
    async def get_layout_tool(_args: dict) -> dict:
        spec = current_layout()
        if spec is None:
            return {"content": [{"type": "text", "text": "No layout applied yet."}]}
        dumped = spec.model_dump(mode="json")
        _populate_effective_titles(dumped["layout"], widget_registry)
        return {"content": [{"type": "text", "text": json.dumps(dumped, indent=2)}]}
    return get_layout_tool
```

`_populate_effective_titles` walks the dumped tree. For each Panel node where `title is None`, it looks up the widget class via `widget_registry.get(panel["widget"])` and calls the same `resolve_title(panel_dict, widget_cls)` helper used by the engine. The result is written back to `panel["title"]`. Containers are recursed into. The walk operates entirely on the dumped dict — no live widgets are touched, no constructors run.

Because `resolve_title` only needs the widget *class* and a `props` dict, the engine path and the `get_layout` path call the same code with the same inputs. They cannot drift.

Title-bearing in-tree widgets:

- Static (`DEFAULT_BORDER_TITLE` only): `OrchestratorChat`, `AgentTable`, `ActivityFeed`, `DiffViewer`.
- Prop-aware (`default_border_title` classmethod): `FileTree`, `FileViewer`, `Markdown`, `LogTail`, `Notebook`, `Terminal`, `AgentTranscript`, `RichTranscript`.

#### Tool description (advertised to the orchestrator)

> Returns the currently applied LayoutSpec as JSON. Each panel's `title` field is populated to its effective on-screen value, so you can match user references like "the Activity Panel" against `title` to find the panel `id` you want to edit. Pass the modified spec back through `set_layout`.

#### `set_layout` description update

Append a sentence noting that each panel accepts an optional `title` field that overrides the widget's default border title, and recommending `get_layout` as the way to discover existing titles before mutating.

### 5. Tests

Additive, no existing test should regress.

- **`test_layout_spec.py`** — `Panel(title=None)` parses; explicit `Panel(title="X")` round-trips through `model_dump` / `model_validate`; unknown extra fields still rejected.
- **New `test_layout_engine_titles.py`** —
  - Explicit `Panel.title` wins over widget defaults.
  - Widget with `default_border_title` classmethod is preferred over one with only `DEFAULT_BORDER_TITLE`.
  - Widget with only `DEFAULT_BORDER_TITLE` uses it.
  - Widget with neither falls back to class name.
  - A widget whose `default_border_title` raises falls back to class name without aborting apply.
  - Safety-net border is applied to a borderless custom widget.
  - Widgets that already declare a border keep their border style.
  - `resolve_title` produces identical output when called by the engine path and by the `get_layout` walk on the same panel.
- **New `test_orchestrator_tools_get_layout.py`** —
  - Returns `"No layout applied yet."` when `current_layout()` returns `None`.
  - Returns the dashboard layout with effective titles populated (`"Orchestrator"`, `"Agents"`, `"Activity"`).
  - An explicit `Panel.title` survives the dump round-trip without being overwritten by the resolver.
- **Update existing widget tests** — any test asserting a borderless widget for `Markdown`, `FileTree`, `Notebook`, `FileViewer`, or `RichTranscript` should expect the new border. (Spot-check during implementation; most tests don't probe styles.)

## Open questions / follow-ups

- In-place title updates that don't remount the panel (preserve scroll/focus/state). Requires switching `apply()` to drive ops produced by `diff()` instead of full rebuilds — broader work.
- `border_subtitle` for live widget status (e.g., "running" / "idle" / row count).
- Title-aware shortcut tools (`set_panel_title`, `set_panel_size`) if `get_layout` + `set_layout` round-trips prove too token-heavy.
