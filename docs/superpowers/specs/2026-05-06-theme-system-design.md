# Theme system

Status: design
Date: 2026-05-06

## Problem

The orchestrator can already mutate the layout (`set_layout`, `save_layout`,
`load_layout`, `list_layouts`) and a saved `default` layout is seeded on first
mount. There is no equivalent for visual styling: every widget pulls colors
from Textual variables (`$primary`, `$surface`, etc.), but those variables
come from Textual's hardcoded default theme, and there is no orchestrator
surface to swap them. A stub `Config.ui.theme = "dark"` exists but is wired
to nothing.

We want to give the orchestrator the same level of control over the look as
it has over the layout: define a theme, save it under a name, load a saved
one, list them, and have a `default` theme that captures the current look so
nothing changes on first boot.

## Goals

- Theme is a saveable, named artifact alongside layouts.
- The orchestrator can introspect, edit, save, load, and apply themes.
- A user-facing modal mirrors `ctrl+l` for layouts.
- Built-in Textual themes (nord, gruvbox, dracula, catppuccin-*, …) are
  reachable through the same `load_theme` tool — saved themes shadow built-in
  names.
- The seeded `default` theme captures the current visual state, so first-run
  is a no-op and the user has a real palette to edit/diff against.
- A bad theme cannot brick boot: corrupted active theme falls back to
  `default`.

## Non-goals

- Per-tab themes. Themes are app-wide.
- Live theme reload on file change. Saved themes are only re-applied when
  explicitly loaded.
- Migration of the dead `ui.theme = "dark"` key. Old configs are accepted; the
  key is silently ignored.

## Architecture

The design is a near-mirror of the layouts subsystem:

| Layouts                              | Themes (this design)                        |
|--------------------------------------|---------------------------------------------|
| `layout/spec.py` → `LayoutSpec`      | `theme/spec.py` → `ThemeSpec`               |
| `layout/engine.py` → `apply`         | `theme/engine.py` → `apply_theme`           |
| `persistence/layouts_store.py`       | `persistence/themes_store.py`               |
| `~/.config/patchbai/layouts/*.json`   | `~/.config/patchbai/themes/*.json`           |
| `set/save/load/list_layout(s)`       | `set/save/load/list_theme(s) + get_theme`   |
| `LayoutSwitcherScreen` on `ctrl+l`   | `ThemeSwitcherScreen` on `ctrl+shift+l`     |
| `default` seeded from `dashboard_layout()` | `default` seeded from current `app.current_theme` |

## Data model — `patchbai/theme/spec.py`

```python
class ThemePalette(BaseModel):
    """Maps 1:1 to textual.theme.Theme constructor args."""
    model_config = ConfigDict(extra="forbid")

    primary: str
    secondary: str | None = None
    warning: str | None = None
    error: str | None = None
    success: str | None = None
    accent: str | None = None
    foreground: str | None = None
    background: str | None = None
    surface: str | None = None
    panel: str | None = None
    boost: str | None = None
    dark: bool = True
    luminosity_spread: float = 0.15
    text_alpha: float = 0.95
    variables: dict[str, str] = Field(default_factory=dict)


class ThemeSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    palette: ThemePalette
    extra_css: str = ""
```

Color strings are not validated up-front — Textual's `Theme(...)` raises on
bad values, surfaced through tool results.

Name validation regex (matching `NamedLayoutsStore`): `^[A-Za-z0-9_\-]+$`.

## Persistence

### `patchbai/persistence/themes_store.py` — `NamedThemesStore`

Direct copy of `NamedLayoutsStore`:

- `__init__(global_dir: Path)` writes under `global_dir / "themes"`.
- `save(name, spec)` validates name regex, writes via `write_json_atomic`.
- `load(name) -> ThemeSpec | None` (logs and returns None on parse failure).
- `list() -> list[str]` (sorted basenames of `*.json`).

### Active-theme pointer

Resolution order on boot: `workspace.active_theme` →
`config.ui.active_theme` → `"default"`.

#### Config (`patchbai/config.py`)

`UISection.theme: str = "dark"` is **dropped** and replaced with
`active_theme: str = "default"`. The `set_path` / `get_path` schema and the
TOML round-trip are updated. Pre-existing TOML files with the old
`ui.theme` key are loaded silently (the key is ignored, no migration).

#### Workspace (`patchbai/workspace/spec.py`)

Add `active_theme: str | None = None` on `Workspace`. `None` means "fall
through to global config."

## Engine — `patchbai/theme/engine.py`

```python
async def apply_theme(app: App, spec: ThemeSpec, *, theme_name: str) -> None:
    """Register/update the theme, set it active, and (re)install extra_css."""
```

Steps in order:

1. **Pre-validate `extra_css`.** Parse it via a throwaway
   `textual.css.stylesheet.Stylesheet().parse(...)` (or equivalent). On
   parse error, raise before touching `app.theme` so the previous theme
   stays active.
2. **Build the Textual `Theme`.** Construct
   `Theme(name=f"patchbai:{theme_name}", **spec.palette.model_dump())`.
3. **Replace any prior registration of the same name.** If
   `f"patchbai:{theme_name}"` is already in `app.available_themes`, call
   `app.unregister_theme(...)` first — Textual's `register_theme` does not
   replace.
4. **Register and activate.** `app.register_theme(theme); app.theme = theme.name`.
   Textual's reactive watcher recomputes `$primary` / `$surface` / etc. and
   refreshes widgets.
5. **Swap the named CSS source.** Maintain a single named source
   `"patchbai:theme"` on `app.stylesheet`. On every apply: drop the old source
   if present, add the new `spec.extra_css` (skip if empty), call
   `app.stylesheet.parse()` and `app.refresh_css()`.
6. **Cache the applied `extra_css`** on `app._active_theme_extra_css` so
   `save_theme` (no-spec form) can snapshot it later. The cache is
   initialized to `""` in `App.__init__` so a snapshot taken before any
   theme has been applied (e.g. while a built-in is active) returns an
   empty string rather than raising.

### Built-in pass-through

`apply_theme` always takes a `ThemeSpec`. The built-in pass-through lives in
the **tool layer** (see below): if `load_theme(name)` finds no saved theme,
and `name` is in `app.available_themes`, the tool sets
`app.theme = name` directly, drops any `patchbai:theme` CSS source, and
clears `app._active_theme_extra_css`. No `Theme` is registered.

### Failure mode

If `Theme(...)` raises (invalid color), or stylesheet parsing raises
(malformed CSS), `apply_theme` raises. Callers (the tool, the modal,
boot) catch and surface the error.

## Orchestrator tools — `patchbai/orchestrator/tools.py`

Five new handlers, gated on `themes_store is not None and app is not None`:

| Tool          | Args                                                           | Behavior |
|---------------|----------------------------------------------------------------|----------|
| `set_theme`   | `spec: dict`                                                   | Validate as `ThemeSpec`, call `apply_theme(app, spec, theme_name="<inline>")`. Does not persist. |
| `save_theme`  | `name: str`, optional `spec: dict`                             | If `spec`, validate and save. If omitted, snapshot the active theme: read `app.current_theme` palette + `app._active_theme_extra_css`, save under `name`. |
| `load_theme`  | `name: str`, optional `persist: bool = true`, optional `scope: "global" \| "project" = "global"` | Look up `name` in store. If missing, fall through to built-ins. Apply. If `persist`, write the new active-theme name to the chosen scope (`config.ui.active_theme` or `workspace.active_theme`). |
| `list_themes` | (none)                                                         | Returns `{"saved": [...], "builtin": [...], "active": "<name>"}`. |
| `get_theme`   | optional `name: str`                                           | If `name` given, return that saved theme's full dump. Otherwise return `{"name": "<active>", "palette": {...}, "extra_css": "..."}` from the live app. |

### Tool description copy

Match the layout tools' tone (`tools.py:471-515`). The `set_theme` /
`save_theme` descriptions include a sentence on `extra_css`:

> If `spec.extra_css` is present, it is parsed and applied at app scope. Bad
> CSS is rejected before the palette change. Only ship `extra_css` you have
> personally authored — CSS can hide chrome, fake widgets, or break input
> visibility.

### Wiring

`build_orchestrator_tools` and `build_orchestrator_mcp_server` gain a
`themes_store: NamedThemesStore | None = None` kwarg. The new tool group
attaches when both `themes_store` and `app` are non-None, mirroring the
layouts gating.

`OrchestratorSession.__init__` (`patchbai/orchestrator/session.py`) gains
the same kwarg and forwards it through.

## Theme switcher modal — `patchbai/widgets/theme_switcher.py`

`ThemeSwitcherScreen(ModalScreen[str | None])` — near copy of
`LayoutSwitcherScreen`:

- Constructor: `(store: NamedThemesStore, available_builtins: list[str], active: str)`.
- Lists saved themes first; then a separator `Label("─ built-ins ─")`; then
  built-ins. The active theme gets a `*` prefix.
- Enter dismisses with the chosen name; Esc dismisses with `None`.

### Action and key binding

- New action `open_theme_switcher` in `PatchbaiApp._register_actions`
  (`app.py:198`).
- Default class binding: `Binding("ctrl+shift+l", "open_theme_switcher",
  "themes")` (added to the `BINDINGS` list at `app.py:134`). Users can
  rebind via `bind_key`.
- `action_show_help` updated to mention `ctrl+shift+l themes`.

### Apply path

`action_open_theme_switcher` pushes the modal; the dismiss callback calls a
new async helper `App._apply_theme_by_name(name, *, scope="global")` that:

1. Looks up the saved theme; if missing, falls back to built-in.
2. Calls `theme.engine.apply_theme` (or sets `app.theme = name` for
   built-ins).
3. Persists the active-theme name to the chosen scope.

This is the same helper `load_theme` calls.

## App boot wiring — `patchbai/app.py`

In `__init__`:

```python
self.themes_store = NamedThemesStore(global_dir=self._global_dir)
```

Pass `themes_store=self.themes_store` to `OrchestratorSession(...)`.

In `on_mount`, after the layouts seed at `app.py:602`:

1. **Seed `default`** if `themes_store.load("default") is None`: snapshot
   `app.current_theme` into a `ThemePalette`, build a `ThemeSpec` with
   empty `extra_css`, save under `"default"`.
2. **Resolve active theme**:
   `name = workspace.active_theme or config.ui.active_theme or "default"`.
3. **Apply it** via `_apply_theme_by_name(name, persist=False)` (no
   persistence; we're just applying what's already persisted). On any
   exception, log it and fall back to `name = "default"`. If even
   `default` fails, log and continue without applying — the app boots with
   raw Textual defaults rather than crashing.

`_apply_theme_by_name(name, *, persist=False, scope="global")` is the
single internal apply helper. The public `load_theme` tool always
calls it with `persist=True`; boot calls it with `persist=False`. `scope`
is only consulted when `persist=True`.

## Tests

New files mirroring the layout test suite:

- `tests/test_theme_spec.py` — palette field defaults, `extra=forbid`, name
  regex on the store side (separate file but exercised here too).
- `tests/test_themes_store.py` — save/load/list, atomic write, bad-name
  rejection, parse-failure returns `None`.
- `tests/test_theme_engine.py` — `apply_theme` registers + activates;
  re-apply replaces; bad CSS raises *before* mutating `app.theme` (assert
  pre-call theme is still active); built-in pass-through clears the
  `patchbai:theme` source.
- `tests/test_orchestrator_tools_theme.py` — one test per tool plus error
  paths (unknown saved name → falls through to built-in, unknown built-in
  → returns error; invalid spec → returns error).
- `tests/test_theme_switcher.py` — modal lists saved + built-ins, active
  prefix marker, Enter dismisses with name, Esc with None.
- `tests/test_app_smoke_theme.py` — boot with empty themes dir seeds
  `default`; boot with `workspace.active_theme="nord"` (built-in) results
  in `app.theme == "nord"`; boot with `config.ui.active_theme="nord"` and
  no workspace override does the same; boot with a corrupted active theme
  falls back to `default` and the app does not crash.

Existing tests touched:

- `tests/test_config_general.py` / `tests/test_config_store.py` — adjust
  for `UISection.active_theme` replacing `theme`. Old TOML with `ui.theme`
  must still parse without raising.
- `tests/test_workspace_spec.py` — assert `Workspace.active_theme` field
  defaults to `None` and round-trips through JSON.

## File map

### New

- `patchbai/theme/__init__.py`
- `patchbai/theme/spec.py`
- `patchbai/theme/engine.py`
- `patchbai/persistence/themes_store.py`
- `patchbai/widgets/theme_switcher.py`
- 6 test files listed above

### Modified

- `patchbai/config.py` — replace `ui.theme` with `ui.active_theme`.
- `patchbai/workspace/spec.py` — add optional `active_theme`.
- `patchbai/orchestrator/tools.py` — 5 new handlers + MCP specs, new kwarg.
- `patchbai/orchestrator/session.py` — forward `themes_store` kwarg.
- `patchbai/app.py` — construct store, boot-apply, modal action, key
  binding, help text.

## Open questions

None.

## Risks

- **Stylesheet API stability.** Textual's `App.stylesheet.add_source` /
  `parse` is used at runtime here. If a future Textual rework breaks this,
  the fallback is to wrap `extra_css` in a CSS layer
  (`@layer patchbai_theme { … }`) and rebuild via the screen-refresh path.
  Not a v1 concern.
- **`extra_css` as a hostile surface.** A malicious theme could hide chrome
  or invert text. Documented in the tool description; same trust posture
  as `custom_widgets` (which executes Python).
- **Boot-time fallback chain.** If both `workspace.active_theme` and
  `config.ui.active_theme` are corrupted, and `default` is also corrupt,
  boot proceeds with raw Textual defaults. The user can recover by deleting
  `~/.config/patchbai/themes/`.
