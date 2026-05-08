# Custom-Widget Loading from Home Directory — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users author Textual widgets in `~/.config/patchbai/widgets/<name>.py` and have patchbai discover, register, and surface them via `list_widgets` at startup so the orchestrator can place them in layouts — without forking the codebase.

**Architecture:** A new `LocalWidgetLoader` walks the home-dir widget folder at app boot, imports each `.py` file as an isolated module, finds its Textual `Widget` subclass via the same precedence rules already used by `register_custom_widget`, and registers it into the `WidgetRegistry`. `WidgetInfo` gains a `source: Literal["builtin", "local", "inline"]` field so `list_widgets` can tell the orchestrator where each widget came from. Per-layout `LayoutSpec.custom_widgets` (inline source) keeps working unchanged and continues to override on name collisions because `register_custom_widget` already calls `unregister(name)` first.

**Tech Stack:** Python 3.11+, `importlib.util.spec_from_file_location`, Textual `Widget`, pydantic, pytest + `pytest-asyncio`.

---

## Executive summary

- **Storage**: `~/.config/patchbai/widgets/` (XDG-compliant; co-resident with `layouts/` and `themes/`). Override via `XDG_CONFIG_HOME` already supported by `global_config_dir()`.
- **Shape**: a single `.py` file per widget, with an optional module-level `__patchbai_widget__` dict for metadata (`name`, `description`, `props_schema`, `entry_point`). No manifest, no package install for v1.
- **Discovery**: auto-walk at startup, register every Widget subclass found, gate the whole feature behind a `widgets.local_dir_enabled` config flag (default `True`).
- **Trust model**: in-process, full privileges, documented in README and a one-line WARN-level startup banner. Sandboxing options explicitly rejected for v1 (subprocess kills Textual integration; RestrictedPython is brittle vs. Textual class hierarchy). No first-run consent prompt.
- **Failures**: a broken widget never crashes patchbai — collect a per-file `LoadOutcome` with status and traceback, expose them through `list_widgets` so the orchestrator (and the user, debugging) can see why something didn't load.

---

## Background — what already exists on this branch

| Concern | Current state |
| --- | --- |
| `WidgetRegistry` (`patchbai/layout/registry.py`) | Maps `name → WidgetInfo(name, cls, description, props_schema)`. Has `register/unregister/get/known/describe/describe_all`. |
| `register_custom_widget` (`patchbai/layout/custom_widgets.py`) | Execs a source string in an empty namespace, picks the Widget subclass via precedence (`WIDGET_CLASS` sentinel → class named after `name` → single Widget subclass), calls `unregister(name)` then `register(...)`. Raises `CustomWidgetError` on failure. Tested in `tests/test_custom_widgets_register.py`. |
| `LayoutSpec.custom_widgets` (`patchbai/layout/spec.py:61-86`) | `list[CustomWidget]` where each has `name: str` + `source: str`. Consumed in `_set_layout_handler` (`patchbai/orchestrator/tools.py:122-148`): every `cw.source` is exec'd into the registry **before** the layout is applied; failure aborts the apply. |
| `build_default_registry()` (`patchbai/app.py:137-219`) | Hard-codes the 12 built-in widgets. |
| `PatchbaiApp.__init__` (`patchbai/app.py:254-308`) | Builds the registry, then constructs the orchestrator (which reads `widget_registry=`). Local widget loading must happen **between** these two steps so the orchestrator's `list_widgets` reflects them. |
| `_list_widgets_handler` (`patchbai/orchestrator/tools.py:287-298`) | Returns `[{name, description, props_schema}]`. No `source` field today. |
| `global_config_dir()` (`patchbai/persistence/paths.py:33-37`) | `$XDG_CONFIG_HOME/patchbai` or `~/.config/patchbai`. |
| `Config` (`patchbai/config.py`) | Two sections (`bindings`, `ui`). `set_path`/`get_path` accept two-segment dotted paths only — perfect fit for `widgets.local_dir_enabled`. |

---

## Design decisions

### 1. Storage location — `~/.config/patchbai/widgets/`

| Candidate | Verdict |
| --- | --- |
| `~/.config/patchbai/widgets/` | **Chosen.** Lives next to existing `layouts/` and `themes/`. XDG-compliant. Reuses `global_config_dir()`. No new path conventions to teach. |
| `~/.patchbai/widgets/` | Rejected — `~/.patchbai/` doesn't exist and `.patchbai/` is reserved for **project-local** state at `<cwd>/.patchbai/`. Reusing the leaf name in `$HOME` would invite confusion. |
| `~/patchbai-widgets/` | Rejected — visible but scattered relative to other patchbai config; a user moving between machines has to back up two directories instead of one. |

`XDG_CONFIG_HOME` already overrides the parent; nothing new is needed for that. The directory is created on demand by the loader (`mkdir(parents=True, exist_ok=True)`); a missing dir is not an error — it just means "no local widgets," which is the default state for a fresh install.

### 2. Widget shape — single `.py` file with optional `__patchbai_widget__` dict

| Option | Verdict |
| --- | --- |
| (a) Single `.py` file | **Chosen.** Lowest friction — write it in any editor, save, restart. Mirrors the existing inline-source pattern. |
| (b) Directory + `manifest.toml` | Deferred to v2 once widgets need bundled assets (CSS, images, multiple modules). The metadata fields (name, description, props_schema, version) can be expressed today as a module-level dict — no need for a separate file. |
| (c) Installable Python package (`pip`-style entry-point) | Deferred to v2+ when the community ecosystem ships. Forces `pip` into the user's authoring loop; needs an entry-point group registered in `pyproject.toml`; unnecessary ceremony for solo authors. |

**On-disk shape:**

```python
# ~/.config/patchbai/widgets/sparkline.py
from textual.widgets import Static

__patchbai_widget__ = {
    "name": "Sparkline",                     # how layouts reference it
    "description": "Render token usage as a sparkline.",
    "props_schema": {"agent_id": str},       # surfaces in list_widgets
    # Optional: explicit entry-point class to skip the heuristic search.
    # "entry_point": Sparkline,
    # Reserved for v2 (currently unused):
    # "version": "0.1.0",
}


class Sparkline(Static):
    def __init__(self, agent_id: str = "") -> None:
        super().__init__()
        self.agent_id = agent_id
```

Both the metadata dict AND the file are optional — a bare file with one `Widget` subclass works:

```python
# ~/.config/patchbai/widgets/banner.py
from textual.widgets import Static

class Banner(Static):
    pass
```

**Class-detection precedence** (mirrors the existing `register_custom_widget` model so authors moving from inline `LayoutSpec.custom_widgets` migrate cleanly):

1. `__patchbai_widget__["entry_point"]` if it's a Widget subclass — **explicit wins**.
2. `WIDGET_CLASS = SomeClass` module-level sentinel.
3. A class whose name matches `__patchbai_widget__["name"]` (or, if no metadata, the filename stem in PascalCase, e.g. `sparkline.py` → `Sparkline`).
4. The single `Widget` subclass **defined in this module** (where `cls.__module__ == module.__name__`). Imported subclasses are NOT candidates — without this filter every `Static` import would compete.
5. Otherwise: record `LoadOutcome.NO_WIDGET_CLASS` (or `AMBIGUOUS_CLASS` if step 4 finds more than one) and skip.

**Why a module-level dict, not class attributes?** Class attributes would force authors to inherit a Patchbai base class or pollute the Textual class with metadata it doesn't care about. A module-level dict keeps metadata declarative and discoverable without instantiating the class — the loader can read `module.__patchbai_widget__` before deciding which class to wire up.

### 3. Registration mechanism — auto-discovery, gated by config

**Chosen: auto-discovery at startup.**

- Walk `~/.config/patchbai/widgets/` (non-recursive — flat directory, one file per widget).
- Skip files whose name starts with `_` or `.` (private/hidden).
- For each surviving `*.py`, import as a module, run the precedence search, register the class.
- The whole feature is gated by `Config.widgets.local_dir_enabled` (default `True`) so a user who wants the feature off can flip a single TOML key — useful for CI, paranoid setups, or debugging registration conflicts.

Auto-discovery loses to explicit allow-lists in adversarial trust scenarios, but **the trust scenario here is "files the user wrote, in the user's home dir, on the user's machine."** That's the same trust scope as `~/.config/patchbai/themes/<n>.json` (which can ship arbitrary CSS) and the existing inline `LayoutSpec.custom_widgets` (which exec's source ad-hoc). Adding an allow-list would punish the 95% case (user wants their widget loaded) to soft-mitigate a threat the user already controls. We can add an allow-list in v2 alongside the **community-installed** widget loader, where the trust boundary actually crosses an authorship line.

### 4. Security model — trust the user, document loudly

Loading arbitrary Python from `~/.config/patchbai/widgets/` is **`exec()`-equivalent** — a custom widget can read files, hit the network, mutate the patchbai process state, and exfiltrate the user's transcripts. We accept this and document it.

**Posture for v1:**

- Treat the home-dir widget folder as a **trusted authorship zone**. The user owns the bytes there. This is the same trust the user already grants to themes (CSS injection), inline `custom_widgets` source (orchestrator-supplied exec), and `config.toml`.
- Print a one-line WARN-level log on every startup that lists the widgets loaded: `INFO patchbai.local_widgets: loaded 3 local widgets from ~/.config/patchbai/widgets (Sparkline, Calendar, GitStatus)`. Not a modal — the user already opted in by writing the files.
- Document the trust model in `README.md` (new "Custom widgets" section) and in a dedicated `docs/superpowers/notes/widget-authoring.md`.

**Sandboxing options considered and rejected for v1:**

| Approach | Why rejected |
| --- | --- |
| Subprocess isolation | The widget IS a Textual class running in patchbai's event loop. Crossing a process boundary breaks the rendering integration entirely. |
| RestrictedPython / `exec` whitelisting | Brittle against Textual's class hierarchy (descriptors, mixins, async tasks). Would block legitimate widgets while still leaking via `__import__` or attribute walks. False security. |
| First-run consent prompt | Adds a modal that gets dismissed by reflex. Provides false security: the user just put the file there themselves. Blocks automated/CI patchbai sessions. Defer until v2 community-installed widgets, where the trust boundary actually shifts. |
| Code signing / signature verification | No PKI to verify against in v1 (no community registry). Adds a step that nobody can satisfy. v2-only when there's a registry to sign against. |

The single mitigation we DO add: a startup log line so the user has a paper trail of what got loaded. This is cheap, useful for debugging, and doesn't pretend to be a security boundary.

### 5. Reload behavior — restart-only

V1: **app restart picks up changes.** The loader runs once in `PatchbaiApp.__init__`. Widgets discovered on restart are seen; edits to an already-loaded file are NOT picked up until the next start.

Why not hot reload:

- Stale class identity: panels already mounted hold references to the old class. Reloading the module gives a new class object; the registry now disagrees with the live tree. Forcing a re-mount would dump panel state.
- `sys.modules` caching, parent-class identity, descriptor identity, signal subscriptions — every one of those is a footgun if we touch it in v1.
- Watcher overhead (watchdog/inotify) brings a new dependency for marginal benefit.

V2 candidate: file-watcher → re-discovery → diff → re-mount of affected panels. Out of scope for this plan.

### 6. Failure handling — never crash patchbai, surface every outcome

A broken widget file MUST NOT prevent app startup. The loader collects a `LoadOutcome` per file:

```python
@dataclass(frozen=True)
class LoadOutcome:
    path: Path                   # absolute path of the .py file
    name: str                    # registered name on success; filename stem on failure
    status: Literal[
        "ok",
        "import_error",          # module raised during import
        "no_widget_class",       # no Widget subclass found
        "ambiguous_class",       # >1 Widget subclasses, no sentinel
        "name_collision",        # name shadows a built-in (skipped, see Q7)
    ]
    error: str | None = None     # short text + first frame of traceback
```

`PatchbaiApp` retains `self._local_widget_outcomes: list[LoadOutcome]`. The orchestrator's `list_widgets` tool gains an optional `errors` field in its JSON output so the orchestrator can introspect failures. Successful registrations are merged into the registry; failures are not.

**Name-collision policy with built-ins:** if a local widget declares `name="OrchestratorChat"` (or any other built-in name), the loader records `LoadOutcome.NAME_COLLISION` and skips the registration. Built-ins win. Rationale: the orchestrator's MCP tool descriptions are pinned to built-in semantics; silently swapping in a user's class breaks the contract advertised to the LLM. Documented; an error-path rather than a footgun.

(Per-layout `LayoutSpec.custom_widgets` is a separate path — see Q7.)

### 7. Reconciliation with `LayoutSpec.custom_widgets` — keep both, inline overrides on collision

Both mechanisms have legitimate, distinct uses:

| | Home-dir widget | Inline `custom_widgets` |
| --- | --- | --- |
| **Author** | The user, ahead of time, in an editor. | The orchestrator, ad-hoc, per-layout. |
| **Lifetime** | App-wide, registered at startup. | Per-`set_layout` call. |
| **Storage** | `~/.config/patchbai/widgets/<n>.py` | Inline string field on `LayoutSpec`. |
| **Reuse** | Reference by name from any layout. | Travels with the spec; saving the layout saves the source. |
| **Use case** | "I want to add a custom Sparkline widget I'll use everywhere." | "Build me a one-off blinking-cursor widget for this specific tab." |

**Recommendation: keep both. Inline wins on name collision.**

Implementation note: this falls out of the existing code with **zero changes** to `_set_layout_handler`. `register_custom_widget` already calls `registry.unregister(name)` before `registry.register(name, ...)`. So when a `set_layout` call references an inline widget that shares a name with a home-dir widget, the inline source replaces the home-dir registration for the lifetime of that registration. Two consequences worth testing:

1. After a `set_layout` with an inline `Fancy`, the inline class is what's mounted (not the home-dir one).
2. If a later `set_layout` lands without a `Fancy` in `custom_widgets`, the inline class **stays registered** — we don't auto-restore the home-dir version. (The orchestrator can re-trigger discovery, but that's v2; for v1 the user can restart, which is consistent with §5.)

The asymmetric collision rule — **built-ins beat home-dir, inline beats home-dir, inline beats built-ins** — is a conscious choice: the more recent/specific the source, the more authoritative. Built-ins still win against home-dir because the orchestrator's advertised tool semantics depend on them.

**Migration story for existing inline-source users:** None today (no shipped users). Going forward, the README's "Custom widgets" section will recommend "if you find yourself repeating the same `custom_widgets` block in many layouts, lift the source to `~/.config/patchbai/widgets/<n>.py`."

### 8. `list_widgets` and orchestrator visibility

Each `WidgetInfo` gains a `source: Literal["builtin", "local", "inline"]` field (default `"builtin"`). The MCP tool's payload becomes:

```json
[
  {
    "name": "OrchestratorChat",
    "description": "...",
    "props_schema": {},
    "source": "builtin"
  },
  {
    "name": "Sparkline",
    "description": "Render token usage as a sparkline.",
    "props_schema": {"agent_id": "str"},
    "source": "local"
  }
]
```

We also add a sibling key on the JSON envelope (NOT a new MCP tool) for failed loads:

```json
{
  "widgets": [...as above...],
  "errors": [
    {"path": "~/.config/patchbai/widgets/broken.py",
     "status": "import_error",
     "error": "SyntaxError: invalid syntax (broken.py, line 3)"}
  ]
}
```

The current `list_widgets` envelope is a plain JSON array of widget objects. We change the response shape — this is a single in-process MCP tool with no external consumers, so a one-line breaking change is fine. The MCP tool description is updated to advertise the new `errors` field. (If we wanted a strictly additive change, we could keep the array shape and tunnel errors via a second tool `list_widget_errors`. Rejected — it scatters the diagnostic info the orchestrator wants in one place.)

**How custom widgets declare metadata:** the loader reads `module.__patchbai_widget__` (a `dict`) — see §2 for the shape. Class attributes were considered (`Sparkline.PATCHBAI_DESCRIPTION = "..."`); rejected because they pollute Textual's class namespace and force per-class wiring even when a module has only one widget.

### 9. Distribution / sharing — leave the door open for v2

V1 builds NO distribution mechanism. The v1 design avoids painting v2 into a corner by:

- **Pluggable loader interface.** `LocalWidgetLoader` is the v1 implementation of an implicit `WidgetLoader` shape (`load() -> list[LoadOutcome]`). v2 `PackageLoader` (entry-points), `RepoLoader` (clone-from-git), `RegistryLoader` (curated server) all conform without rewriting registration.
- **Stable metadata schema.** The `__patchbai_widget__` dict is the lingua franca. v2 manifests (TOML, package distribution metadata) feed into the same dict shape so the orchestrator's `list_widgets` view never branches.
- **Opaque source identifiers.** `LoadOutcome.path` is `Path` for v1, but `WidgetInfo.source` is the union literal `"builtin" | "local" | "inline"` — adding `"package" | "registry"` later is a string-set extension. Don't bake filesystem paths into the registry.
- **Reserved `version` field.** `__patchbai_widget__["version"]` is unused in v1 but documented as reserved so v2 can introduce dependency/compat checks without a spec break.

**Explicitly out of scope for v1:** signature verification, signed widget bundles, cross-user installation flows, a `patchbai widget install <url>` CLI, dependency resolution, version pinning.

---

## Affected files

| File | Change |
| --- | --- |
| `patchbai/persistence/paths.py` | **+1 function**: `local_widgets_dir(global_dir: Path | None = None) -> Path`. |
| `patchbai/layout/registry.py` | Add `source: Literal["builtin", "local", "inline"] = "builtin"` field to `WidgetInfo`. Extend `register(...)` to accept a `source` keyword. |
| `patchbai/layout/custom_widgets.py` | Pass `source="inline"` through to `registry.register(...)`. Factor the class-detection heuristic into a shared helper module-level function used by both this file AND the new loader. |
| `patchbai/layout/local_widgets.py` | **NEW.** `LoadOutcome` dataclass, `LocalWidgetLoader.load()` method, module import via `importlib.util.spec_from_file_location`, class-detection delegate, name-collision check. |
| `patchbai/config.py` | Add `WidgetsSection(local_dir_enabled: bool = True)` and wire into `Config`, `ConfigStore.load`, `ConfigStore.save`. |
| `patchbai/app.py` | In `PatchbaiApp.__init__`, after `self.registry = registry or build_default_registry()` and before constructing `OrchestratorSession`, call the loader (gated on config). Stash outcomes on `self._local_widget_outcomes`. Annotate built-ins with `source="builtin"` (already the default; no behavior change, just defensive). |
| `patchbai/orchestrator/tools.py` | `_list_widgets_handler`: emit new envelope `{widgets: [...], errors: [...]}` with `source` per widget. Update the SDK tool description string in `build_orchestrator_mcp_server`. |
| `tests/test_local_widgets_loader.py` | **NEW.** Unit tests for the loader: discovery, precedence, metadata parsing, error collection, missing-dir behavior, name-collision-with-builtins. |
| `tests/test_layout_registry_source_field.py` | **NEW.** Verifies `WidgetInfo.source` round-trips through `register/describe/describe_all`. |
| `tests/test_orchestrator_tools_list_widgets.py` | Extend existing tests to cover the new `{widgets, errors}` envelope and `source` field. |
| `tests/test_app_smoke_local_widgets.py` | **NEW.** Integration smoke: drop a widget file under `tmp_path/.config/patchbai/widgets/`, set `XDG_CONFIG_HOME=tmp_path/.config`, boot `PatchbaiApp`, verify the widget is registered AND can be referenced from a `LayoutSpec`. |
| `README.md` | New "Custom widgets" subsection with the trust-model warning + a 10-line authoring example. |
| `docs/superpowers/notes/widget-authoring.md` | **NEW.** Author guide: shape, metadata, precedence, common errors, troubleshooting. |

---

## Test strategy

**Unit (`tests/test_local_widgets_loader.py`):**
- Empty directory yields `[]` and does not raise.
- Missing directory (never created) yields `[]` and does not raise.
- Single bare `.py` with one Widget subclass → `OK` outcome, registered under filename stem.
- File with `__patchbai_widget__` metadata → registered under metadata `name`, with `description` + `props_schema` propagated.
- File with `entry_point` set → registered to that exact class.
- File with `WIDGET_CLASS` sentinel → registered to that class.
- File with two Widget subclasses, no sentinel/entry_point → `ambiguous_class` outcome, NOT registered.
- File with no Widget subclass → `no_widget_class` outcome.
- File that raises during import (`raise RuntimeError("nope")` at module scope) → `import_error` outcome with traceback in `error`.
- File whose name shadows a built-in (e.g. metadata `name="OrchestratorChat"`) → `name_collision` outcome, NOT registered. Built-in still in registry afterwards.
- Files starting with `_` or `.` are skipped silently.

**Unit (`tests/test_layout_registry_source_field.py`):**
- Default `source` is `"builtin"`.
- `register(..., source="local")` → `describe(name).source == "local"`.
- `describe_all()` preserves `source`.

**Unit (extension to `tests/test_orchestrator_tools_list_widgets.py`):**
- Output envelope now has `widgets` and `errors` keys.
- Each widget object has `source`.
- A registry pre-loaded with one local widget AND one error-outcome (synthesized via a helper since the handler shouldn't import the loader directly) emits both.
- Snapshot the envelope shape so future drift is caught.

**Integration (`tests/test_app_smoke_local_widgets.py`):**
- `tmp_path / ".config" / "patchbai" / "widgets" / "banner.py"` written on disk.
- `monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))`.
- `PatchbaiApp(...)` constructed; assert `app.registry.get("Banner")` returns the loaded class.
- Apply a `LayoutSpec` referencing `Banner` via the orchestrator tools; assert it mounts (smoke-level, using `app.run_test()` like the other `test_app_smoke_*.py` tests).

**Integration (negative-path):**
- Drop a syntactically broken widget; boot the app; assert it starts cleanly and the broken file is reported in `app._local_widget_outcomes` with `status="import_error"`.

---

## Implementation order

Each task is a self-contained TDD cycle. Each commit leaves the build green.

### Task 1: Add `source` field to `WidgetInfo`

**Files:**
- Modify: `patchbai/layout/registry.py`
- Test: `tests/test_layout_registry_source_field.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_layout_registry_source_field.py
from textual.widget import Widget

from patchbai.layout.registry import WidgetRegistry


class _W(Widget):
    pass


def test_register_default_source_is_builtin():
    reg = WidgetRegistry()
    reg.register("X", _W)
    assert reg.describe("X").source == "builtin"


def test_register_with_explicit_source():
    reg = WidgetRegistry()
    reg.register("X", _W, source="local")
    assert reg.describe("X").source == "local"


def test_describe_all_preserves_source():
    reg = WidgetRegistry()
    reg.register("A", _W, source="builtin")
    reg.register("B", _W, source="local")
    sources = {info.name: info.source for info in reg.describe_all()}
    assert sources == {"A": "builtin", "B": "local"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_layout_registry_source_field.py -v`
Expected: FAIL with `TypeError: register() got an unexpected keyword argument 'source'` and `AttributeError: 'WidgetInfo' object has no attribute 'source'`.

- [ ] **Step 3: Add the field and parameter**

In `patchbai/layout/registry.py`:

```python
from typing import Literal

WidgetSource = Literal["builtin", "local", "inline"]


@dataclass(frozen=True)
class WidgetInfo:
    name: str
    cls: type[Widget]
    description: str = ""
    props_schema: dict = field(default_factory=dict)
    source: WidgetSource = "builtin"


class WidgetRegistry:
    # ... unchanged docstring ...

    def __init__(self) -> None:
        self._infos: dict[str, WidgetInfo] = {}

    def register(
        self,
        name: str,
        cls: type[Widget],
        *,
        description: str = "",
        props_schema: dict | None = None,
        source: WidgetSource = "builtin",
    ) -> None:
        self._infos[name] = WidgetInfo(
            name=name, cls=cls,
            description=description,
            props_schema=dict(props_schema) if props_schema else {},
            source=source,
        )
```

- [ ] **Step 4: Verify all registry tests pass**

Run: `uv run pytest tests/test_layout_registry_source_field.py tests/test_layout_registry_unregister.py tests/test_custom_widgets_register.py -v`
Expected: PASS for all.

- [ ] **Step 5: Commit**

```bash
git add patchbai/layout/registry.py tests/test_layout_registry_source_field.py
git commit -m "feat(registry): add source field to WidgetInfo"
```

---

### Task 2: Mark inline custom widgets with `source="inline"`

**Files:**
- Modify: `patchbai/layout/custom_widgets.py:44-49`
- Test: `tests/test_custom_widgets_register.py` (extend existing file)

- [ ] **Step 1: Add a failing test to the existing file**

Append to `tests/test_custom_widgets_register.py`:

```python
def test_register_custom_widget_marks_source_inline():
    reg = WidgetRegistry()
    src = """
from textual.widgets import Static
class Banner(Static):
    pass
"""
    register_custom_widget(reg, "Banner", src)
    assert reg.describe("Banner").source == "inline"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_custom_widgets_register.py::test_register_custom_widget_marks_source_inline -v`
Expected: FAIL with `assert 'builtin' == 'inline'`.

- [ ] **Step 3: Pass `source="inline"` through**

In `patchbai/layout/custom_widgets.py`, change the `registry.register(...)` call:

```python
    registry.unregister(name)
    registry.register(
        name, cls,
        description=description,
        props_schema=props_schema or {},
        source="inline",
    )
```

- [ ] **Step 4: Verify**

Run: `uv run pytest tests/test_custom_widgets_register.py -v`
Expected: PASS for all (existing tests unchanged + the new one).

- [ ] **Step 5: Commit**

```bash
git add patchbai/layout/custom_widgets.py tests/test_custom_widgets_register.py
git commit -m "feat(custom-widgets): tag inline registrations with source=inline"
```

---

### Task 3: Add `widgets.local_dir_enabled` config flag

**Files:**
- Modify: `patchbai/config.py`
- Test: `tests/test_config_widgets_section.py` (NEW)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config_widgets_section.py
from patchbai.config import Config, ConfigStore


def test_widgets_section_defaults_to_enabled():
    cfg = Config()
    assert cfg.widgets.local_dir_enabled is True


def test_get_path_widgets_local_dir_enabled():
    cfg = Config()
    assert cfg.get_path("widgets.local_dir_enabled") is True


def test_set_path_widgets_local_dir_enabled(tmp_path):
    store = ConfigStore(global_dir=tmp_path)
    cfg = store.load()
    cfg.set_path("widgets.local_dir_enabled", False)
    store.save(cfg)
    reloaded = store.load()
    assert reloaded.widgets.local_dir_enabled is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config_widgets_section.py -v`
Expected: FAIL with `AttributeError: 'Config' object has no attribute 'widgets'`.

- [ ] **Step 3: Add the section**

In `patchbai/config.py`:

```python
@dataclass
class WidgetsSection:
    local_dir_enabled: bool = True


@dataclass
class Config:
    bindings: dict[str, KeyBinding] = field(default_factory=dict)
    ui: UISection = field(default_factory=UISection)
    widgets: WidgetsSection = field(default_factory=WidgetsSection)
    # ... existing get_path / set_path / _split_path unchanged ...
```

In `ConfigStore.load`, after the `ui_raw` block:

```python
        widgets_raw = raw.get("widgets", {})
        if isinstance(widgets_raw, dict):
            if "local_dir_enabled" in widgets_raw and isinstance(
                widgets_raw["local_dir_enabled"], bool
            ):
                cfg.widgets.local_dir_enabled = widgets_raw["local_dir_enabled"]
        return cfg
```

In `ConfigStore.save`, extend the `out` dict:

```python
        out = {
            "bindings": {...},
            "ui": {...},
            "widgets": {
                "local_dir_enabled": cfg.widgets.local_dir_enabled,
            },
        }
```

- [ ] **Step 4: Verify**

Run: `uv run pytest tests/test_config_widgets_section.py tests/test_config*.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add patchbai/config.py tests/test_config_widgets_section.py
git commit -m "feat(config): add widgets.local_dir_enabled (default true)"
```

---

### Task 4: Add `local_widgets_dir()` path helper

**Files:**
- Modify: `patchbai/persistence/paths.py`
- Test: `tests/test_paths_local_widgets_dir.py` (NEW)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_paths_local_widgets_dir.py
from pathlib import Path

from patchbai.persistence.paths import local_widgets_dir


def test_local_widgets_dir_under_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert local_widgets_dir() == tmp_path / "patchbai" / "widgets"


def test_local_widgets_dir_default(monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert local_widgets_dir() == tmp_path / ".config" / "patchbai" / "widgets"


def test_local_widgets_dir_explicit_global_dir(tmp_path):
    assert local_widgets_dir(global_dir=tmp_path) == tmp_path / "widgets"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_paths_local_widgets_dir.py -v`
Expected: FAIL with `ImportError: cannot import name 'local_widgets_dir'`.

- [ ] **Step 3: Add the function**

In `patchbai/persistence/paths.py`:

```python
def local_widgets_dir(global_dir: Path | None = None) -> Path:
    """Return the directory where user-authored custom widgets live.

    With `global_dir` provided, returns `<global_dir>/widgets/` — useful for
    tests that pin a per-tmp_path config root. Without it, derives from
    `global_config_dir()` (which honors `XDG_CONFIG_HOME`).
    """
    base = Path(global_dir) if global_dir else global_config_dir()
    return base / "widgets"
```

- [ ] **Step 4: Verify**

Run: `uv run pytest tests/test_paths_local_widgets_dir.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add patchbai/persistence/paths.py tests/test_paths_local_widgets_dir.py
git commit -m "feat(paths): add local_widgets_dir() helper"
```

---

### Task 5: Build the `LocalWidgetLoader`

This is the largest task; we sub-divide its TDD cycle into discovery, then precedence, then error handling, then collision.

**Files:**
- Create: `patchbai/layout/local_widgets.py`
- Test: `tests/test_local_widgets_loader.py`

- [ ] **Step 1: Write the loader's first failing test (happy path)**

```python
# tests/test_local_widgets_loader.py
from pathlib import Path

import pytest

from patchbai.layout.local_widgets import LocalWidgetLoader, LoadOutcome
from patchbai.layout.registry import WidgetRegistry


def _write(p: Path, body: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


def test_loader_registers_single_widget(tmp_path):
    _write(tmp_path / "banner.py", """
from textual.widgets import Static

class Banner(Static):
    pass
""")
    reg = WidgetRegistry()
    outcomes = LocalWidgetLoader(tmp_path, reg).load()
    assert [o.status for o in outcomes] == ["ok"]
    assert reg.get("Banner").__name__ == "Banner"
    assert reg.describe("Banner").source == "local"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_local_widgets_loader.py::test_loader_registers_single_widget -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'patchbai.layout.local_widgets'`.

- [ ] **Step 3: Implement the minimum loader**

Create `patchbai/layout/local_widgets.py`:

```python
from __future__ import annotations

import importlib.util
import logging
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from textual.widget import Widget

from patchbai.layout.registry import WidgetRegistry

log = logging.getLogger(__name__)


LoadStatus = Literal[
    "ok",
    "import_error",
    "no_widget_class",
    "ambiguous_class",
    "name_collision",
]


@dataclass(frozen=True)
class LoadOutcome:
    path: Path
    name: str
    status: LoadStatus
    error: str | None = None


class LocalWidgetLoader:
    """Walks a directory, imports every top-level .py file as an isolated
    module, finds its Textual Widget subclass, and registers it.

    Files starting with '_' or '.' are skipped.
    Missing/empty directory yields an empty outcome list (not an error).
    """

    def __init__(self, dir_path: Path, registry: WidgetRegistry) -> None:
        self._dir = Path(dir_path)
        self._registry = registry

    def load(self) -> list[LoadOutcome]:
        if not self._dir.exists():
            return []
        outcomes: list[LoadOutcome] = []
        for path in sorted(self._dir.iterdir()):
            if not path.is_file() or path.suffix != ".py":
                continue
            if path.name.startswith(("_", ".")):
                continue
            outcomes.append(self._load_one(path))
        return outcomes

    def _load_one(self, path: Path) -> LoadOutcome:
        stem = path.stem
        # Use a unique module name to avoid collisions in sys.modules.
        mod_name = f"_patchbai_local_widget_{stem}"
        try:
            spec = importlib.util.spec_from_file_location(mod_name, path)
            assert spec is not None and spec.loader is not None
            module = importlib.util.module_from_spec(spec)
            sys.modules[mod_name] = module
            spec.loader.exec_module(module)
        except Exception:
            tb = traceback.format_exc(limit=2)
            return LoadOutcome(
                path=path, name=stem, status="import_error", error=tb,
            )

        meta = getattr(module, "__patchbai_widget__", {}) or {}
        if not isinstance(meta, dict):
            meta = {}
        name = meta.get("name") or _pascal(stem)

        # Name-collision with an already-registered builtin: skip.
        existing = self._registry._infos.get(name)
        if existing is not None and existing.source == "builtin":
            return LoadOutcome(
                path=path, name=name, status="name_collision",
                error=f"name {name!r} is reserved by the built-in registry",
            )

        cls, err = _find_widget_class_in_module(module, meta, name)
        if cls is None:
            return LoadOutcome(path=path, name=name, status=err or "no_widget_class")

        self._registry.register(
            name, cls,
            description=meta.get("description", ""),
            props_schema=dict(meta.get("props_schema") or {}),
            source="local",
        )
        return LoadOutcome(path=path, name=name, status="ok")


def _pascal(stem: str) -> str:
    return "".join(part.capitalize() for part in stem.replace("-", "_").split("_") if part)


def _find_widget_class_in_module(module, meta: dict, name: str):
    """Returns (cls_or_none, status_on_failure)."""
    # 1. Explicit entry_point in metadata.
    ep = meta.get("entry_point")
    if isinstance(ep, type) and issubclass(ep, Widget):
        return ep, None

    # 2. WIDGET_CLASS sentinel.
    sentinel = getattr(module, "WIDGET_CLASS", None)
    if isinstance(sentinel, type) and issubclass(sentinel, Widget):
        return sentinel, None

    # 3. Class named exactly `name`.
    by_name = getattr(module, name, None)
    if isinstance(by_name, type) and issubclass(by_name, Widget):
        return by_name, None

    # 4. Single Widget subclass DEFINED in this module.
    candidates = []
    for v in vars(module).values():
        if (
            isinstance(v, type)
            and issubclass(v, Widget)
            and v is not Widget
            and getattr(v, "__module__", None) == module.__name__
        ):
            candidates.append(v)
    unique = list({id(c): c for c in candidates}.values())
    if len(unique) == 1:
        return unique[0], None
    if len(unique) > 1:
        return None, "ambiguous_class"
    return None, "no_widget_class"
```

- [ ] **Step 4: Run the happy-path test**

Run: `uv run pytest tests/test_local_widgets_loader.py::test_loader_registers_single_widget -v`
Expected: PASS.

- [ ] **Step 5: Add tests for metadata, entry_point, and skip rules**

```python
def test_loader_uses_metadata_name_description_props(tmp_path):
    _write(tmp_path / "spark.py", """
from textual.widgets import Static

__patchbai_widget__ = {
    "name": "Sparkline",
    "description": "Token sparkline.",
    "props_schema": {"agent_id": str},
}

class Sparkline(Static):
    pass
""")
    reg = WidgetRegistry()
    LocalWidgetLoader(tmp_path, reg).load()
    info = reg.describe("Sparkline")
    assert info.description == "Token sparkline."
    assert info.props_schema == {"agent_id": str}


def test_loader_honors_entry_point_metadata(tmp_path):
    _write(tmp_path / "x.py", """
from textual.widgets import Static

class Real(Static):
    pass

class Decoy(Static):
    pass

__patchbai_widget__ = {"name": "X", "entry_point": Real}
""")
    reg = WidgetRegistry()
    outcomes = LocalWidgetLoader(tmp_path, reg).load()
    assert outcomes[0].status == "ok"
    assert reg.get("X").__name__ == "Real"


def test_loader_honors_widget_class_sentinel(tmp_path):
    _write(tmp_path / "y.py", """
from textual.widgets import Static

class Picked(Static):
    pass

class Other(Static):
    pass

WIDGET_CLASS = Picked
""")
    reg = WidgetRegistry()
    LocalWidgetLoader(tmp_path, reg).load()
    assert reg.get("Y").__name__ == "Picked"


def test_loader_pascal_cases_filename_stem(tmp_path):
    _write(tmp_path / "git_status.py", """
from textual.widgets import Static
class GitStatus(Static):
    pass
""")
    reg = WidgetRegistry()
    LocalWidgetLoader(tmp_path, reg).load()
    assert "GitStatus" in reg.known()


def test_loader_skips_underscore_and_dot_files(tmp_path):
    _write(tmp_path / "_hidden.py", "x = 1")
    _write(tmp_path / ".dot.py", "x = 1")
    reg = WidgetRegistry()
    outcomes = LocalWidgetLoader(tmp_path, reg).load()
    assert outcomes == []


def test_loader_missing_dir_returns_empty(tmp_path):
    reg = WidgetRegistry()
    outcomes = LocalWidgetLoader(tmp_path / "does_not_exist", reg).load()
    assert outcomes == []
```

- [ ] **Step 6: Run them**

Run: `uv run pytest tests/test_local_widgets_loader.py -v`
Expected: PASS for all six.

- [ ] **Step 7: Add error-path tests**

```python
def test_loader_records_import_error(tmp_path):
    _write(tmp_path / "broken.py", "this is not valid python\n")
    reg = WidgetRegistry()
    outcomes = LocalWidgetLoader(tmp_path, reg).load()
    assert len(outcomes) == 1
    assert outcomes[0].status == "import_error"
    assert outcomes[0].error and "SyntaxError" in outcomes[0].error
    assert "broken" not in reg.known() and "Broken" not in reg.known()


def test_loader_records_no_widget_class(tmp_path):
    _write(tmp_path / "nowidget.py", "x = 42\n")
    reg = WidgetRegistry()
    outcomes = LocalWidgetLoader(tmp_path, reg).load()
    assert outcomes[0].status == "no_widget_class"


def test_loader_records_ambiguous_class(tmp_path):
    _write(tmp_path / "two.py", """
from textual.widgets import Static
class A(Static):
    pass
class B(Static):
    pass
""")
    reg = WidgetRegistry()
    outcomes = LocalWidgetLoader(tmp_path, reg).load()
    assert outcomes[0].status == "ambiguous_class"


def test_loader_skips_name_collision_with_builtin(tmp_path):
    from textual.widgets import Static
    reg = WidgetRegistry()
    reg.register("OrchestratorChat", Static)  # builtin (default source)

    _write(tmp_path / "evil.py", """
from textual.widgets import Static

__patchbai_widget__ = {"name": "OrchestratorChat"}

class Evil(Static):
    pass
""")
    outcomes = LocalWidgetLoader(tmp_path, reg).load()
    assert outcomes[0].status == "name_collision"
    # Builtin still wins.
    assert reg.get("OrchestratorChat") is Static
```

- [ ] **Step 8: Run all loader tests**

Run: `uv run pytest tests/test_local_widgets_loader.py -v`
Expected: PASS for all 10 tests.

- [ ] **Step 9: Commit**

```bash
git add patchbai/layout/local_widgets.py tests/test_local_widgets_loader.py
git commit -m "feat(local-widgets): loader for ~/.config/patchbai/widgets"
```

---

### Task 6: Wire the loader into `PatchbaiApp.__init__`

**Files:**
- Modify: `patchbai/app.py` (around line 271, after `self.registry = registry or build_default_registry()`)
- Test: `tests/test_app_smoke_local_widgets.py` (NEW)

- [ ] **Step 1: Write the failing integration test**

```python
# tests/test_app_smoke_local_widgets.py
from pathlib import Path

import pytest

from patchbai.agents.fake_sdk_adapter import FakeSDKAdapter
from patchbai.agents.manager import AgentManager
from patchbai.app import PatchbaiApp
from patchbai.events import EventBus


def _ok_script():
    from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock
    return [
        AssistantMessage(content=[TextBlock(text="ok")], model="fake-model"),
        ResultMessage(
            subtype="success", duration_ms=1, duration_api_ms=1,
            is_error=False, num_turns=1, session_id="fake",
            total_cost_usd=0.0, usage={"input_tokens": 1, "output_tokens": 1},
            result="ok",
        ),
    ]


def _write_widget(global_dir: Path, name: str, body: str) -> None:
    wdir = global_dir / "widgets"
    wdir.mkdir(parents=True, exist_ok=True)
    (wdir / f"{name}.py").write_text(body, encoding="utf-8")


@pytest.mark.asyncio
async def test_app_loads_local_widget_into_registry(tmp_path):
    _write_widget(tmp_path, "banner", """
from textual.widgets import Static
class Banner(Static):
    pass
""")
    bus = EventBus()
    manager = AgentManager(
        cwd=tmp_path, bus=bus,
        adapter_factory=lambda: FakeSDKAdapter(scripts=[_ok_script()]),
    )
    app = PatchbaiApp(cwd=tmp_path, manager=manager, global_dir=tmp_path)
    assert "Banner" in app.registry.known()
    assert app.registry.describe("Banner").source == "local"
    assert any(o.status == "ok" for o in app._local_widget_outcomes)


@pytest.mark.asyncio
async def test_app_survives_broken_local_widget(tmp_path):
    _write_widget(tmp_path, "broken", "this is not valid python\n")
    bus = EventBus()
    manager = AgentManager(
        cwd=tmp_path, bus=bus,
        adapter_factory=lambda: FakeSDKAdapter(scripts=[_ok_script()]),
    )
    app = PatchbaiApp(cwd=tmp_path, manager=manager, global_dir=tmp_path)
    statuses = [o.status for o in app._local_widget_outcomes]
    assert "import_error" in statuses
    # And the rest of the app constructs cleanly.
    assert "OrchestratorChat" in app.registry.known()


@pytest.mark.asyncio
async def test_app_skips_loading_when_disabled(tmp_path):
    _write_widget(tmp_path, "banner", """
from textual.widgets import Static
class Banner(Static):
    pass
""")
    # Pre-write a config that disables the feature.
    (tmp_path / "config.toml").write_text(
        "[widgets]\nlocal_dir_enabled = false\n", encoding="utf-8",
    )
    bus = EventBus()
    manager = AgentManager(
        cwd=tmp_path, bus=bus,
        adapter_factory=lambda: FakeSDKAdapter(scripts=[_ok_script()]),
    )
    app = PatchbaiApp(cwd=tmp_path, manager=manager, global_dir=tmp_path)
    assert "Banner" not in app.registry.known()
    assert app._local_widget_outcomes == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_app_smoke_local_widgets.py -v`
Expected: FAIL with `AttributeError: 'PatchbaiApp' object has no attribute '_local_widget_outcomes'` (and `Banner` not registered).

- [ ] **Step 3: Wire the loader into `PatchbaiApp.__init__`**

In `patchbai/app.py`, after the imports add:

```python
from patchbai.layout.local_widgets import LocalWidgetLoader, LoadOutcome
from patchbai.persistence.paths import local_widgets_dir
```

In `PatchbaiApp.__init__`, replace the line `self.registry = registry or build_default_registry()` with:

```python
        self.registry = registry or build_default_registry()
        self._local_widget_outcomes: list[LoadOutcome] = []
        # Load user-authored widgets from `~/.config/patchbai/widgets/` UNLESS
        # the caller passed in their own registry (test/embedded use case) OR
        # the widgets.local_dir_enabled flag is False.
        if registry is None:
            cfg = self.config_store.load() if False else None  # placeholder; see below
```

Wait — `self.config_store` is constructed a few lines later. We have to either move config-store construction up OR construct a local `ConfigStore` here. Pick the latter to keep ordering changes minimal:

```python
        self.registry = registry or build_default_registry()
        self._local_widget_outcomes: list[LoadOutcome] = []
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
```

NOTE: `self._global_dir` is assigned a few lines below the registry construction in the current code. Move the line `self._global_dir = Path(global_dir) if global_dir else global_config_dir()` ABOVE the registry-construction block so this works. The patch is:

1. Move `self._global_dir = Path(global_dir) if global_dir else global_config_dir()` to be the FIRST line that follows `super().__init__()` (right after `_active_theme_extra_css` setup and the `self.cwd = ...` line).
2. Then construct registry.
3. Then run the loader.
4. Then continue with the existing `self.config_store = ...` etc.

A `log = logging.getLogger(__name__)` already exists at the top of `app.py` if not, add one.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_app_smoke_local_widgets.py -v`
Expected: PASS for all three tests.

- [ ] **Step 5: Run the full test suite to catch regressions**

Run: `uv run pytest -x -q`
Expected: PASS for all existing tests. `test_app_smoke_*` cases that pass `global_dir=tmp_path` already pin a clean directory so the loader is a no-op there.

- [ ] **Step 6: Commit**

```bash
git add patchbai/app.py tests/test_app_smoke_local_widgets.py
git commit -m "feat(app): load local widgets at startup, gated by config"
```

---

### Task 7: Surface `source` and `errors` in `list_widgets` MCP tool

**Files:**
- Modify: `patchbai/orchestrator/tools.py:287-298` and `patchbai/orchestrator/tools.py:796-803`
- Test: extend `tests/test_orchestrator_tools_list_widgets.py`

- [ ] **Step 1: Read the existing tests to understand the assertion shape**

Run: `uv run pytest tests/test_orchestrator_tools_list_widgets.py -v` (just to see what's there).
Inspect `tests/test_orchestrator_tools_list_widgets.py` and note the existing assertion patterns. The plan author has confirmed they reference the array-shaped envelope; we are intentionally breaking that shape.

- [ ] **Step 2: Update existing tests to expect the new envelope**

Replace assertions of the form `assert isinstance(payload, list)` with `assert "widgets" in payload and "errors" in payload`. Each existing test that asserts a widget by index needs to re-target `payload["widgets"][i]`. (Keep this Step focused: make every existing test fail in the way the new shape requires; then add the new tests in Step 3.)

- [ ] **Step 3: Add new failing assertions**

Append to `tests/test_orchestrator_tools_list_widgets.py`:

```python
@pytest.mark.asyncio
async def test_list_widgets_emits_source_field(...):
    # Build a registry with one builtin and one local widget; call the tool;
    # assert each widget has `source` reflecting its origin.
    ...


@pytest.mark.asyncio
async def test_list_widgets_emits_errors_array(...):
    # Pass a fake errors list through the tool's surfacing path; assert
    # the JSON envelope includes them under `errors`.
    ...
```

(Each `...` is to be filled in by the implementer following the existing test fixtures in this file.)

- [ ] **Step 4: Run to confirm failures**

Run: `uv run pytest tests/test_orchestrator_tools_list_widgets.py -v`
Expected: FAIL — old shape vs. new shape, plus missing `source`/`errors`.

- [ ] **Step 5: Update the handler**

Change `_list_widgets_handler` in `patchbai/orchestrator/tools.py`:

```python
def _list_widgets_handler(registry: WidgetRegistry, outcomes_provider=None):
    """outcomes_provider: optional zero-arg callable returning a list of
    LoadOutcome (or compatible dicts). When provided, failed outcomes are
    emitted under `errors`. When None, `errors` is `[]`.
    """
    async def list_widgets_tool(_args: dict) -> dict:
        widgets = []
        for info in registry.describe_all():
            widgets.append({
                "name": info.name,
                "description": info.description,
                "props_schema": {
                    k: getattr(v, "__name__", str(v))
                    for k, v in info.props_schema.items()
                },
                "source": info.source,
            })
        errors = []
        if outcomes_provider is not None:
            for o in outcomes_provider():
                if getattr(o, "status", "ok") == "ok":
                    continue
                errors.append({
                    "path": str(getattr(o, "path", "")),
                    "name": getattr(o, "name", ""),
                    "status": getattr(o, "status", ""),
                    "error": getattr(o, "error", None),
                })
        envelope = {"widgets": widgets, "errors": errors}
        return {"content": [{"type": "text", "text": json.dumps(envelope, indent=2)}]}
    return list_widgets_tool
```

Update `build_orchestrator_tools(...)` and `build_orchestrator_mcp_server(...)` to thread an `outcomes_provider` through. The cleanest plumbing: `app._local_widget_outcomes` is a list, so:

```python
    if widget_registry is not None:
        provider = (lambda: getattr(app, "_local_widget_outcomes", []))
        handlers["list_widgets"] = _list_widgets_handler(widget_registry, provider)
```

Update the MCP tool description string in `build_orchestrator_mcp_server` to describe the new envelope shape:

```python
        sdk_tools.append(tool(
            "list_widgets",
            "List all widgets registered in the layout registry. Returns "
            "{widgets: [{name, description, props_schema, source}], "
            "errors: [{path, name, status, error}]}. `source` is one of "
            "'builtin' (compiled-in), 'local' (loaded from "
            "~/.config/patchbai/widgets/), or 'inline' (registered via a "
            "LayoutSpec.custom_widgets source). `errors` lists widget files "
            "in the local dir that failed to load.",
            {},
        )(_list_widgets_handler(widget_registry, provider)))
```

- [ ] **Step 6: Run the tool tests + the full suite**

Run: `uv run pytest tests/test_orchestrator_tools_list_widgets.py -v`
Expected: PASS.

Then: `uv run pytest -x -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add patchbai/orchestrator/tools.py tests/test_orchestrator_tools_list_widgets.py
git commit -m "feat(orchestrator): list_widgets emits source and errors envelope"
```

---

### Task 8: README + author guide

**Files:**
- Modify: `README.md`
- Create: `docs/superpowers/notes/widget-authoring.md`

- [ ] **Step 1: Add a "Custom widgets" subsection to `README.md`**

Insert after the existing "Why use it" section (or wherever the user's table of contents lives). Cover: storage path (`~/.config/patchbai/widgets/`), shape (single `.py` + optional `__patchbai_widget__` dict), the trust warning ("loaded with full process privileges; only put files there you wrote"), reload behavior (restart-only), how to disable (`widgets.local_dir_enabled = false`).

- [ ] **Step 2: Write `docs/superpowers/notes/widget-authoring.md`**

Cover the same material in depth: precedence rules, common pitfalls (multiple Widget subclasses without sentinel; collision with built-ins), how to verify your widget loaded (`list_widgets` from the orchestrator), how to debug an `import_error`. Include the full `Sparkline` example and a tabular reference of the `__patchbai_widget__` dict keys.

- [ ] **Step 3: Verify the docs build / link-check**

There's no docs build target. Manually confirm both files render in your editor's markdown preview.

- [ ] **Step 4: Commit**

```bash
git add README.md docs/superpowers/notes/widget-authoring.md
git commit -m "docs: custom widgets — storage, shape, trust model, authoring"
```

---

### Task 9: End-to-end sanity check

**Files:** none — this is a manual/automated final sweep.

- [ ] **Step 1: Run the full test suite**

Run: `uv run pytest -q`
Expected: PASS, with `test_app_smoke_local_widgets.py` and `test_local_widgets_loader.py` included.

- [ ] **Step 2: Run pyright**

Run: `uv run pyright patchbai/layout/local_widgets.py patchbai/layout/registry.py patchbai/layout/custom_widgets.py patchbai/app.py patchbai/orchestrator/tools.py`
Expected: 0 errors. Address any type-narrowing issues by adding annotations.

- [ ] **Step 3: Smoke-test interactively**

```bash
mkdir -p ~/.config/patchbai/widgets
cat > ~/.config/patchbai/widgets/banner.py <<'EOF'
from textual.widgets import Static

__patchbai_widget__ = {
    "name": "Banner",
    "description": "Hello-world local widget.",
}

class Banner(Static):
    DEFAULT_CSS = "Banner { content-align: center middle; color: $success; }"

    def render(self) -> str:
        return "✨ local widget loaded ✨"
EOF
uv run patchbai
```

In the orchestrator chat: "list_widgets" — confirm `Banner` appears with `source: local`.
Then: "set_layout to a horizontal split with OrchestratorChat at 60% and Banner at 40%" — confirm the panel mounts.

Restore your widgets dir afterwards (or just delete `banner.py`).

- [ ] **Step 4: No commit needed** (this is verification only).

---

## Out of scope for v1

Documented to keep scope creep at bay during review:

- **Hot reload** of widget files. Restart-only is the v1 contract (§5). Watchdog-based reload + selective re-mount lands in v2.
- **Sandboxing.** Subprocess isolation, RestrictedPython, capability filtering — all rejected (§4) on cost-vs-value grounds. The v1 trust model is "user owns the bytes."
- **Distribution / community registry.** No `patchbai widget install`, no signed bundles, no curated index. The on-disk shape is forward-compatible (§9) for v2 to add a `PackageLoader` without rewriting registration.
- **Manifest files** (`manifest.toml`). The metadata dict in-source is the v1 path; manifests arrive when widgets need bundled assets.
- **Pip-installable widgets.** Same reasoning — the loader interface accepts new sources without breaking the v1 file-based one.
- **Per-project (cwd-local) widgets.** v1 is global only. A project-local `<cwd>/.patchbai/widgets/` would be useful for sharing widgets via git, but it complicates trust (now you load a teammate's code on `cd`). Defer until we have an answer for that.
- **First-run consent prompt.** Rejected for v1 (§4); revisit when authorship boundary changes (community widgets).
- **Widget versioning / compat checks.** `__patchbai_widget__["version"]` is reserved but unused.
- **Hot-reload of `widgets.local_dir_enabled`.** The flag is read once at startup; flipping it requires restart.

---

## Plan complete

Saved to `docs/superpowers/plans/2026-05-07-widget-repo.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using `superpowers:executing-plans`, batched checkpoints for review.

Which approach?
