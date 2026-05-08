# Authoring custom widgets

This guide covers the on-disk custom-widget loader. The companion
runtime path — Python emitted in `LayoutSpec.custom_widgets` — is not
covered here; it's for one-off widgets the orchestrator throws away
between layouts.

## Where they live

```
~/.config/patchbai/widgets/<name>.py
```

The directory is auto-created if missing. Files starting with `_` or
`.` are skipped (handy for staging `_wip.py` or vendoring deps under
`_helpers.py`). One file per widget; the loader does not recurse into
subdirectories.

The directory is resolved through `local_widgets_dir()` in
`patchbai/persistence/paths.py`, which respects `XDG_CONFIG_HOME` and
the `PATCHBAI_GLOBAL_DIR` test override.

## Shape of a widget file

A widget file is a normal Python module that:

1. Imports `textual.widget.Widget` (or any subclass — `Static`,
   `Container`, `DataTable`, etc.).
2. Defines exactly one `Widget` subclass at module scope (or uses
   one of the disambiguators below).
3. Optionally declares a `__patchbai_widget__` dict at module scope.

```python
from textual.widgets import Static

__patchbai_widget__ = {
    "name": "Sparkline",
    "description": "Compact unicode sparkline of a numeric series.",
    "props_schema": {"values": list, "width": int},
}

class Sparkline(Static):
    BLOCKS = " ▁▂▃▄▅▆▇█"

    def __init__(self, values: list[float] | None = None,
                 width: int = 40, **kw) -> None:
        super().__init__("", **kw)
        self._values = values or []
        self._width = width

    def on_mount(self) -> None:
        self.refresh_render()

    def refresh_render(self) -> None:
        if not self._values:
            self.update("(no data)")
            return
        lo, hi = min(self._values), max(self._values)
        rng = hi - lo or 1.0
        chars = []
        for v in self._values[-self._width:]:
            idx = int((v - lo) / rng * (len(self.BLOCKS) - 1))
            chars.append(self.BLOCKS[idx])
        self.update("".join(chars))
```

Drop that into `~/.config/patchbai/widgets/sparkline.py`, restart, and
ask: *"set a layout with a Sparkline showing values=[1,2,3,5,8,13,21]
on the right at 25%."*

## Class-detection precedence

When a file defines multiple classes (helpers, base classes, etc.) the
loader walks four rules in order and stops at the first match:

1. **`entry_point` in `__patchbai_widget__`.** If you set
   `"entry_point": MyWidget`, the loader registers exactly that class.
   This wins over everything else.
2. **Module-level `WIDGET_CLASS = ...` sentinel.** Same idea, no dict
   required. `WIDGET_CLASS = MyWidget` at the bottom of the file is
   often the cleanest way.
3. **Class named the same as the registered widget.** If the resolved
   name is `Sparkline` and the module defines `class Sparkline(Static)`,
   that class wins — *but only if it's defined in the module itself*.
   An imported `Sparkline` doesn't count (see pitfalls).
4. **Single Widget subclass defined in the module.** If exactly one
   class in the file inherits from `Widget` (transitively) and was
   defined in this module, it wins by default. Imported subclasses are
   ignored for this count too.

If none of these resolve a unique class, the loader emits an
`ambiguous_class` or `no_widget_class` outcome and skips the file.

## Common pitfalls

**Multiple Widget subclasses, no sentinel.** A file with two helper
widgets and no `entry_point` / `WIDGET_CLASS` / name match will fail
with `ambiguous_class`. Pick one of the disambiguators above.

**Collision with a built-in.** If you name your widget `FileTree`, the
loader sees the existing built-in registration and *skips your file* —
the built-in keeps the name. This shows up in `list_widgets`'s
`errors` array as `name_collision`. Pick a unique name.

**Importing a Widget subclass that isn't yours.** Earlier versions of
the loader counted imported `Widget` subclasses toward the
single-Widget-subclass rule, which meant `from textual.widgets import
Static` could pin your file's identity to `Static`. The loader now
filters by `__module__`, so only classes *defined in your file* count.
If you're vendoring helper classes, leave them imported normally —
they're invisible to the resolver.

**Top-level side effects.** The module is `exec`'d at startup. Don't
do network calls, write files, or `print()` from module scope. Wrap
that work in `on_mount` or a method called from a layout prop.

**Bad imports.** A missing import yields an `import_error` outcome
with a 2-frame traceback in the `error` field. Look at `list_widgets`
to see it.

## Verifying it loaded

Ask the orchestrator (or call the tool yourself):

> *"list_widgets"*

The response is an envelope:

```json
{
  "widgets": [
    {"name": "Sparkline", "description": "...",
     "props_schema": {"values": "list", "width": "int"},
     "source": "local"},
    {"name": "FileTree", "description": "...",
     "props_schema": {...}, "source": "builtin"}
  ],
  "errors": [
    {"path": "/Users/me/.config/patchbai/widgets/broken.py",
     "name": "Broken", "status": "import_error",
     "error": "ModuleNotFoundError: No module named 'foo'\n  File ..."}
  ]
}
```

`source: "local"` confirms your widget loaded. The `errors` array
holds **startup-time discovery failures only** — the loader walks the
directory once at app launch. Fix the file, restart, and the error
clears (or moves to a different one).

`save_widget` rejections do not appear in `errors`; they come back as
the tool result string immediately. That's the only place to look for
them.

## Debugging an `import_error`

1. Read the `error` field — it's the last 2 frames of the traceback,
   usually enough to spot a missing `from textual.widgets import ...`
   or a typo.
2. Run the file standalone: `uv run python ~/.config/patchbai/widgets/yourname.py`.
   This won't instantiate the widget, but it *will* surface SyntaxErrors
   and import problems.
3. If the file imports a third-party package (anything beyond
   `textual` and the stdlib), make sure it's installed in the same
   environment patchbai runs from. If you launched with `uv run patchbai`,
   that's the project's `.venv`. `uv add <pkg>` if you need it.

## When to write the file vs. ask the orchestrator

The orchestrator has a `save_widget` MCP tool that:

- Validates the source via the same class-detection precedence above
  (in a temp directory — no side-effects on failure).
- Atomically writes the file to `~/.config/patchbai/widgets/<name>.py`.
- Registers it into the live registry, so you can put it in the next
  `set_layout` call without restarting.

Use `save_widget` when you're iterating on a widget *with the
orchestrator in the loop* — it's strictly better than asking it to
`Write` the file (which would skip live-registration and require a
restart).

Write the file yourself when you're authoring offline (in your editor,
without a running patchbai session), or when the source is large
enough that you'd rather not paste it through chat. After saving,
restart patchbai and the loader picks it up.

The orchestrator will not silently re-author your file — `save_widget`
is the single channel for orchestrator-authored persistence.

## `__patchbai_widget__` reference

| Key            | Type                | Purpose                                                                                                                                                |
| -------------- | ------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `name`         | `str`               | Registered widget name. Defaults to the PascalCased file stem (`token_chart.py` → `TokenChart`).                                                       |
| `description`  | `str`               | One-line description shown in `list_widgets`. Optional but recommended — the orchestrator uses it to decide when to mount the widget.                  |
| `props_schema` | `dict[str, type]`   | Map of prop name → Python type. Surfaces in `list_widgets` (types are stringified) so the orchestrator knows what `props` shape `set_layout` accepts.  |
| `entry_point`  | `type[Widget]`      | Explicit class to register. Highest precedence in class detection. Use when your file defines multiple Widget subclasses.                              |
| `version`      | reserved            | Reserved for a future widget-versioning story. Not read today; pick a sane value (`1`) if you want it; do not rely on any behavior tied to it.         |

Anything else in the dict is silently ignored — keep it lean.

## Disabling local widgets

For security review, or when handing the laptop to someone else,
disable the loader entirely:

```toml
# ~/.config/patchbai/config.toml
[widgets]
local_dir_enabled = false
```

With this set, the directory is never walked at startup, regardless of
its contents. The `save_widget` tool still works (it writes to the same
directory), but on the *next* startup the file won't be re-imported
until you flip the flag back. There's no on-disk runtime override —
the flag is read at app launch.
