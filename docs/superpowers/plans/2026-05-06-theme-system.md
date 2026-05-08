# Theme System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the orchestrator a saved-theme system that mirrors the existing saved-layout system: ThemeSpec, NamedThemesStore, set/save/load/list/get tools, a switcher modal, and a `default` theme seeded from the live look on first boot.

**Architecture:** Near-mirror of the layouts subsystem. A `ThemeSpec` (palette + extra_css) is stored at `~/.config/patchfeld/themes/<name>.json`. `apply_theme(app, spec, theme_name=...)` registers a Textual `Theme`, sets it active, and (re)installs `extra_css` as a single named source on `app.stylesheet`. Built-in Textual themes (nord, gruvbox, dracula, …) are reachable through `load_theme` via a fall-through path. Active theme name is persisted in `workspace.active_theme` (project) → `config.ui.active_theme` (global) → `"default"`.

**Tech Stack:** Python 3.12, Textual (with `textual.theme.Theme`, `App.register_theme`, `App.stylesheet`), Pydantic v2, pytest + pytest-asyncio, `uv` for env management.

**Reference spec:** `docs/superpowers/specs/2026-05-06-theme-system-design.md`

**Run tests with:** `uv run pytest <path> -v`

---

## Task 1: ThemeSpec data model

**Files:**
- Create: `patchfeld/theme/__init__.py`
- Create: `patchfeld/theme/spec.py`
- Test: `tests/test_theme_spec.py`

- [ ] **Step 1: Create empty package `__init__`**

Create `patchfeld/theme/__init__.py` with no content (empty file).

- [ ] **Step 2: Write failing tests for ThemeSpec**

Create `tests/test_theme_spec.py`:

```python
import pytest
from pydantic import ValidationError

from patchfeld.theme.spec import ThemePalette, ThemeSpec


def test_theme_palette_requires_primary():
    with pytest.raises(ValidationError):
        ThemePalette()  # type: ignore[call-arg]


def test_theme_palette_minimal():
    pal = ThemePalette(primary="#005577")
    assert pal.primary == "#005577"
    assert pal.dark is True
    assert pal.luminosity_spread == 0.15
    assert pal.text_alpha == 0.95
    assert pal.variables == {}
    assert pal.secondary is None


def test_theme_palette_extra_forbidden():
    with pytest.raises(ValidationError):
        ThemePalette(primary="#005577", bogus="x")  # type: ignore[call-arg]


def test_theme_spec_minimal():
    spec = ThemeSpec(palette=ThemePalette(primary="#005577"))
    assert spec.version == 1
    assert spec.extra_css == ""
    assert spec.palette.primary == "#005577"


def test_theme_spec_full_round_trip():
    raw = {
        "version": 1,
        "palette": {
            "primary": "#005577",
            "secondary": "#0099aa",
            "warning": "#ffaa00",
            "error": "#ff0033",
            "success": "#00aa55",
            "accent": "#cc66ff",
            "foreground": "#ffffff",
            "background": "#0a0a0a",
            "surface": "#1a1a1a",
            "panel": "#222222",
            "boost": "#333333",
            "dark": True,
            "luminosity_spread": 0.2,
            "text_alpha": 0.9,
            "variables": {"my-var": "#ff00ff"},
        },
        "extra_css": "OrchestratorChat { border: round $accent; }",
    }
    spec = ThemeSpec.model_validate(raw)
    assert spec.model_dump(mode="json") == raw


def test_theme_spec_extra_forbidden():
    with pytest.raises(ValidationError):
        ThemeSpec.model_validate({
            "palette": {"primary": "#005577"},
            "bogus": True,
        })
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_theme_spec.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'patchfeld.theme.spec'` or import errors.

- [ ] **Step 4: Implement `patchfeld/theme/spec.py`**

```python
from pydantic import BaseModel, ConfigDict, Field


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
    """Saved theme. Applied to a live App via theme.engine.apply_theme."""
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    palette: ThemePalette
    extra_css: str = ""
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_theme_spec.py -v`
Expected: PASS, all 5 tests green.

- [ ] **Step 6: Commit**

```bash
git add patchfeld/theme/__init__.py patchfeld/theme/spec.py tests/test_theme_spec.py
git commit -m "feat(theme): ThemeSpec/ThemePalette pydantic models"
```

---

## Task 2: NamedThemesStore persistence

**Files:**
- Create: `patchfeld/persistence/themes_store.py`
- Test: `tests/test_themes_store.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_themes_store.py`:

```python
from pathlib import Path

import pytest

from patchfeld.persistence.themes_store import NamedThemesStore
from patchfeld.theme.spec import ThemePalette, ThemeSpec


def _spec() -> ThemeSpec:
    return ThemeSpec(palette=ThemePalette(primary="#005577"))


def test_save_and_load_round_trip(tmp_path: Path):
    store = NamedThemesStore(global_dir=tmp_path)
    store.save("nord-ish", _spec())
    assert store.load("nord-ish") == _spec()


def test_load_missing_returns_none(tmp_path: Path):
    store = NamedThemesStore(global_dir=tmp_path)
    assert store.load("nope") is None


def test_save_creates_themes_dir(tmp_path: Path):
    store = NamedThemesStore(global_dir=tmp_path)
    store.save("triage", _spec())
    assert (tmp_path / "themes" / "triage.json").exists()


def test_list_returns_saved_names_sorted(tmp_path: Path):
    store = NamedThemesStore(global_dir=tmp_path)
    store.save("c", _spec())
    store.save("a", _spec())
    store.save("b", _spec())
    assert store.list() == ["a", "b", "c"]


def test_load_invalid_file_returns_none(tmp_path: Path):
    themes = tmp_path / "themes"
    themes.mkdir()
    (themes / "broken.json").write_text("not json {{")
    store = NamedThemesStore(global_dir=tmp_path)
    assert store.load("broken") is None


def test_save_rejects_invalid_name(tmp_path: Path):
    store = NamedThemesStore(global_dir=tmp_path)
    with pytest.raises(ValueError):
        store.save("../escape", _spec())
    with pytest.raises(ValueError):
        store.save("name/with/slashes", _spec())
    with pytest.raises(ValueError):
        store.save("", _spec())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_themes_store.py -v`
Expected: FAIL with import error.

- [ ] **Step 3: Implement `patchfeld/persistence/themes_store.py`**

```python
import json
import logging
import re
from pathlib import Path

from patchfeld.persistence.atomic import write_json_atomic
from patchfeld.theme.spec import ThemeSpec

log = logging.getLogger(__name__)

_VALID_NAME = re.compile(r"^[A-Za-z0-9_\-]+$")


class NamedThemesStore:
    """Read/write named ThemeSpecs at <global_dir>/themes/<name>.json."""

    def __init__(self, global_dir: Path) -> None:
        self._dir = Path(global_dir) / "themes"

    def save(self, name: str, spec: ThemeSpec) -> None:
        if not name or not _VALID_NAME.match(name):
            raise ValueError(
                f"theme name must match {_VALID_NAME.pattern!r}, got {name!r}"
            )
        write_json_atomic(self._dir / f"{name}.json", spec.model_dump(mode="json"))

    def load(self, name: str) -> ThemeSpec | None:
        path = self._dir / f"{name}.json"
        if not path.exists():
            return None
        try:
            return ThemeSpec.model_validate(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            log.exception("Failed to load named theme %r", name)
            return None

    def list(self) -> list[str]:
        if not self._dir.exists():
            return []
        names = []
        for p in self._dir.iterdir():
            if p.is_file() and p.suffix == ".json":
                names.append(p.stem)
        return sorted(names)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_themes_store.py -v`
Expected: PASS, all 6 tests green.

- [ ] **Step 5: Commit**

```bash
git add patchfeld/persistence/themes_store.py tests/test_themes_store.py
git commit -m "feat(persistence): NamedThemesStore mirroring NamedLayoutsStore"
```

---

## Task 3: Replace `ui.theme` with `ui.active_theme` in Config

**Files:**
- Modify: `patchfeld/config.py`
- Test: `tests/test_config_general.py` (read-only — verify how field is exercised)
- Test: `tests/test_config_store.py` (modify if it asserts on `theme`)

- [ ] **Step 1: Find existing assertions on `ui.theme`**

Run: `grep -rn "ui\.theme\|ui_theme\|theme.*=.*['\"]dark['\"]\|UISection" /Users/jimmy.mills/Developer/patchfeld/tests /Users/jimmy.mills/Developer/patchfeld/patchfeld --include="*.py"`

Note any tests that assert on the `theme` field.

- [ ] **Step 2: Write failing tests for new field**

Append to `tests/test_config_store.py` (or create if absent — but it should exist; check first with `ls tests/test_config_store.py`):

```python
from pathlib import Path

from patchfeld.config import ConfigStore


def test_active_theme_defaults_to_default(tmp_path: Path):
    store = ConfigStore(global_dir=tmp_path)
    cfg = store.load()
    assert cfg.ui.active_theme == "default"


def test_active_theme_persists_round_trip(tmp_path: Path):
    store = ConfigStore(global_dir=tmp_path)
    cfg = store.load()
    cfg.ui.active_theme = "nord"
    store.save(cfg)

    reloaded = ConfigStore(global_dir=tmp_path).load()
    assert reloaded.ui.active_theme == "nord"


def test_legacy_ui_theme_key_silently_ignored(tmp_path: Path):
    """Old configs with `ui.theme = "dark"` must still load without raising;
    the dead key is just ignored."""
    (tmp_path / "config.toml").write_text(
        '[ui]\ntheme = "dark"\ndefault_model = ""\n',
        encoding="utf-8",
    )
    store = ConfigStore(global_dir=tmp_path)
    cfg = store.load()
    assert cfg.ui.active_theme == "default"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_config_store.py -v -k "active_theme or legacy_ui_theme"`
Expected: FAIL — `active_theme` attribute does not exist.

- [ ] **Step 4: Update `patchfeld/config.py`**

In `patchfeld/config.py`, replace the `UISection` dataclass and the `load`/`save` TOML logic:

```python
@dataclass
class UISection:
    active_theme: str = "default"
    default_model: str = ""
```

In `ConfigStore.load`, replace the `ui_raw` block:

```python
        ui_raw = raw.get("ui", {})
        if isinstance(ui_raw, dict):
            if "active_theme" in ui_raw and isinstance(ui_raw["active_theme"], str):
                cfg.ui.active_theme = ui_raw["active_theme"]
            if "default_model" in ui_raw and isinstance(ui_raw["default_model"], str):
                cfg.ui.default_model = ui_raw["default_model"]
            # Legacy `ui.theme` key (now removed) is silently ignored.
        return cfg
```

In `ConfigStore.save`, replace the `out` dict:

```python
        out = {
            "bindings": {
                key: {"action": b.action, "args": b.args}
                for key, b in cfg.bindings.items()
            },
            "ui": {
                "active_theme": cfg.ui.active_theme,
                "default_model": cfg.ui.default_model,
            },
        }
```

- [ ] **Step 5: Update existing tests that asserted on `ui.theme`**

Look at the grep output from Step 1. For any test that referenced `cfg.ui.theme`, update it to `cfg.ui.active_theme` (or drop the assertion if the test wasn't really about that field).

If `tests/test_config_general.py` references the old field, update it. If it doesn't, skip this step.

- [ ] **Step 6: Run all config tests**

Run: `uv run pytest tests/test_config_store.py tests/test_config_general.py -v`
Expected: PASS — all new tests green, no regressions.

- [ ] **Step 7: Commit**

```bash
git add patchfeld/config.py tests/test_config_store.py
# Also include tests/test_config_general.py if you modified it.
git commit -m "feat(config): replace ui.theme with ui.active_theme (default 'default')"
```

---

## Task 4: Add `Workspace.active_theme` field

**Files:**
- Modify: `patchfeld/workspace/spec.py`
- Test: `tests/test_workspace_spec.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_workspace_spec.py`:

```python
def test_workspace_active_theme_defaults_to_none():
    ws = Workspace.model_validate({
        "version": 1,
        "tabs": [
            {"id": "t1", "title": "Main", "layout": _layout_with_chat()},
        ],
        "active": "t1",
    })
    assert ws.active_theme is None


def test_workspace_active_theme_round_trips():
    ws = Workspace.model_validate({
        "version": 1,
        "tabs": [
            {"id": "t1", "title": "Main", "layout": _layout_with_chat()},
        ],
        "active": "t1",
        "active_theme": "nord",
    })
    assert ws.active_theme == "nord"
    assert ws.model_dump(mode="json")["active_theme"] == "nord"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_workspace_spec.py -v -k "active_theme"`
Expected: FAIL — field does not exist (Pydantic forbids extra).

- [ ] **Step 3: Add the field to `Workspace`**

In `patchfeld/workspace/spec.py`, modify the `Workspace` class to add `active_theme`:

```python
class Workspace(BaseModel):
    """Top-level container. Holds a list of Tabs and an active id.

    Invariants:
    - Non-empty tab list.
    - `active` references one of `tabs[].id`.
    - At least one OrchestratorChat panel exists across all tabs combined.
    - Tab ids are unique.
    """
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    tabs: list[Tab] = Field(min_length=1)
    active: str
    active_theme: str | None = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_workspace_spec.py -v`
Expected: PASS — all tests green, including pre-existing ones.

- [ ] **Step 5: Commit**

```bash
git add patchfeld/workspace/spec.py tests/test_workspace_spec.py
git commit -m "feat(workspace): add optional active_theme override field"
```

---

## Task 5: Theme engine (apply_theme)

**Files:**
- Create: `patchfeld/theme/engine.py`
- Test: `tests/test_theme_engine.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_theme_engine.py`:

```python
import pytest
from textual.app import App

from patchfeld.theme.engine import apply_theme
from patchfeld.theme.spec import ThemePalette, ThemeSpec


def _spec(primary: str = "#005577", extra_css: str = "") -> ThemeSpec:
    return ThemeSpec(palette=ThemePalette(primary=primary), extra_css=extra_css)


@pytest.mark.asyncio
async def test_apply_theme_registers_and_activates():
    class _Host(App):
        pass

    host = _Host()
    async with host.run_test():
        await apply_theme(host, _spec(primary="#112233"), theme_name="alpha")
        assert host.theme == "patchfeld:alpha"
        assert "patchfeld:alpha" in host.available_themes


@pytest.mark.asyncio
async def test_apply_theme_replaces_existing_registration():
    """Re-applying with the same name must not raise (Textual's
    register_theme would raise on duplicate; engine handles unregister)."""
    class _Host(App):
        pass

    host = _Host()
    async with host.run_test():
        await apply_theme(host, _spec(primary="#111111"), theme_name="alpha")
        # Mutate palette and re-apply with same name.
        await apply_theme(host, _spec(primary="#222222"), theme_name="alpha")
        assert host.theme == "patchfeld:alpha"


@pytest.mark.asyncio
async def test_apply_theme_installs_extra_css_source():
    class _Host(App):
        pass

    host = _Host()
    async with host.run_test():
        await apply_theme(
            host,
            _spec(extra_css="OrchestratorChat { border: round $accent; }"),
            theme_name="alpha",
        )
        keys = list(host.stylesheet.source.keys())
        assert ("patchfeld_theme", "extra_css") in keys


@pytest.mark.asyncio
async def test_apply_theme_swaps_extra_css_source():
    """A second apply must remove the previous extra_css before installing the new one."""
    class _Host(App):
        pass

    host = _Host()
    async with host.run_test():
        await apply_theme(
            host, _spec(extra_css="A { color: $accent; }"),
            theme_name="alpha",
        )
        await apply_theme(
            host, _spec(extra_css="B { color: $accent; }"),
            theme_name="alpha",
        )
        key = ("patchfeld_theme", "extra_css")
        assert key in host.stylesheet.source
        css = host.stylesheet.source[key].content
        assert "B {" in css
        assert "A {" not in css


@pytest.mark.asyncio
async def test_apply_theme_drops_extra_css_when_empty():
    class _Host(App):
        pass

    host = _Host()
    async with host.run_test():
        await apply_theme(
            host, _spec(extra_css="A { color: $accent; }"),
            theme_name="alpha",
        )
        await apply_theme(host, _spec(extra_css=""), theme_name="alpha")
        assert ("patchfeld_theme", "extra_css") not in host.stylesheet.source


@pytest.mark.asyncio
async def test_apply_theme_caches_extra_css_on_app():
    class _Host(App):
        pass

    host = _Host()
    async with host.run_test():
        await apply_theme(
            host, _spec(extra_css="X { color: red; }"),
            theme_name="alpha",
        )
        assert host._active_theme_extra_css == "X { color: red; }"
        await apply_theme(host, _spec(extra_css=""), theme_name="alpha")
        assert host._active_theme_extra_css == ""


@pytest.mark.asyncio
async def test_apply_theme_bad_css_raises_before_mutating_app_theme():
    """Malformed CSS must be rejected before app.theme is reassigned."""
    class _Host(App):
        pass

    host = _Host()
    async with host.run_test():
        original_theme = host.theme
        bad_css = "this is not valid css {{{"
        with pytest.raises(Exception):
            await apply_theme(
                host, _spec(extra_css=bad_css), theme_name="alpha",
            )
        assert host.theme == original_theme
        assert "patchfeld:alpha" not in host.available_themes


@pytest.mark.asyncio
async def test_apply_theme_bad_palette_color_raises():
    class _Host(App):
        pass

    host = _Host()
    async with host.run_test():
        original_theme = host.theme
        bad_spec = ThemeSpec(palette=ThemePalette(primary="not-a-color"))
        with pytest.raises(Exception):
            await apply_theme(host, bad_spec, theme_name="alpha")
        assert host.theme == original_theme
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_theme_engine.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'patchfeld.theme.engine'`.

- [ ] **Step 3: Implement `patchfeld/theme/engine.py`**

```python
"""Apply a ThemeSpec to a live Textual App.

apply_theme() is the single seam every theme-load path goes through:
    - the load_theme orchestrator tool,
    - the theme switcher modal,
    - boot-time apply.

The function is idempotent under same-name re-apply.
"""
from textual.app import App
from textual.css.stylesheet import Stylesheet
from textual.theme import Theme

from patchfeld.theme.spec import ThemeSpec

_EXTRA_CSS_KEY = ("patchfeld_theme", "extra_css")
_THEME_NAME_PREFIX = "patchfeld:"


async def apply_theme(app: App, spec: ThemeSpec, *, theme_name: str) -> None:
    """Register/update the theme, set it active, and (re)install extra_css.

    Order of operations: validate everything that can fail BEFORE mutating
    app.theme. If anything raises, the previous theme stays active.
    """
    # 1. Pre-validate extra_css by attempting to parse it on a throwaway sheet.
    if spec.extra_css:
        probe = Stylesheet()
        probe.add_source(spec.extra_css, read_from=_EXTRA_CSS_KEY)
        probe.parse()  # raises on syntax errors

    # 2. Build the Textual Theme. Will raise on bad color strings.
    full_name = f"{_THEME_NAME_PREFIX}{theme_name}"
    theme = Theme(name=full_name, **spec.palette.model_dump())

    # 3. Replace any prior registration for this name. register_theme would
    #    raise on duplicate, and we want re-apply to mean "swap in place."
    if full_name in app.available_themes:
        app.unregister_theme(full_name)

    # 4. Register and activate. Reactive watcher refreshes $primary etc.
    app.register_theme(theme)
    app.theme = full_name

    # 5. Swap the named CSS source.
    if _EXTRA_CSS_KEY in app.stylesheet.source:
        del app.stylesheet.source[_EXTRA_CSS_KEY]
    if spec.extra_css:
        app.stylesheet.add_source(spec.extra_css, read_from=_EXTRA_CSS_KEY)
    app.refresh_css()

    # 6. Cache the applied extra_css for snapshotting (save_theme).
    app._active_theme_extra_css = spec.extra_css
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_theme_engine.py -v`
Expected: PASS, all 8 tests green.

If `test_apply_theme_bad_palette_color_raises` does not raise (Textual may accept "not-a-color" without raising at construction time), change the test color to something that is structurally invalid — `Theme()`'s constructor accepts strings opaquely; if Textual is permissive here, the test should be deleted and a comment added in `engine.py` noting that palette validation is best-effort. Run again and confirm the rest pass.

- [ ] **Step 5: Commit**

```bash
git add patchfeld/theme/engine.py tests/test_theme_engine.py
git commit -m "feat(theme): apply_theme engine with extra_css source management"
```

---

## Task 6: Initialize `_active_theme_extra_css` cache on App

**Files:**
- Modify: `patchfeld/app.py:153-194` (the `__init__` method)

- [ ] **Step 1: Add the field initializer to `PatchfeldApp.__init__`**

In `patchfeld/app.py`, near the top of `__init__` after `super().__init__()` (line 162), add:

```python
        # Cache for the currently-applied theme's extra_css. Initialized to
        # "" so save_theme can snapshot a clean state before any apply runs.
        self._active_theme_extra_css: str = ""
```

(No test here — exercised by Task 5's tests once Task 7 wires the engine in.)

- [ ] **Step 2: Run app smoke tests to confirm no regression**

Run: `uv run pytest tests/test_app_smoke.py -v`
Expected: PASS (no behavior change yet).

- [ ] **Step 3: Commit**

```bash
git add patchfeld/app.py
git commit -m "chore(app): init _active_theme_extra_css cache slot"
```

---

## Task 7: Orchestrator tools (5 new handlers)

**Files:**
- Modify: `patchfeld/orchestrator/tools.py` (handler functions + `_SPECS`-style wiring + `build_orchestrator_tools` + `build_orchestrator_mcp_server`)
- Test: `tests/test_orchestrator_tools_theme.py`

- [ ] **Step 1: Write failing tests for the 5 tools (handler-level)**

Create `tests/test_orchestrator_tools_theme.py`:

```python
import json
from pathlib import Path

import pytest
from textual.app import App

from patchfeld.agents.fake_sdk_adapter import FakeSDKAdapter
from patchfeld.agents.manager import AgentManager
from patchfeld.events import EventBus
from patchfeld.orchestrator.tools import build_orchestrator_tools
from patchfeld.persistence.themes_store import NamedThemesStore
from patchfeld.theme.spec import ThemePalette, ThemeSpec


def _make_manager(tmp_path, ok_script):
    return AgentManager(
        cwd=tmp_path,
        bus=EventBus(),
        adapter_factory=lambda: FakeSDKAdapter(scripts=[ok_script()]),
    )


def _spec_dict(primary: str = "#112233") -> dict:
    return ThemeSpec(palette=ThemePalette(primary=primary)).model_dump(mode="json")


class _StubApp(App):
    """Bare App used as the `app` arg for tool wiring. Avoids spinning up
    the full PatchfeldApp."""


@pytest.mark.asyncio
async def test_set_theme_invokes_apply_theme(tmp_path, ok_script):
    manager = _make_manager(tmp_path, ok_script)
    store = NamedThemesStore(global_dir=tmp_path)
    host = _StubApp()
    async with host.run_test():
        host._active_theme_extra_css = ""
        tools = build_orchestrator_tools(
            manager, themes_store=store, app=host,
        )
        out = await tools["set_theme"]({"spec": _spec_dict("#aabbcc")})
        assert "applied" in out["content"][0]["text"].lower()
        assert host.theme.startswith("patchfeld:")


@pytest.mark.asyncio
async def test_set_theme_with_invalid_spec_returns_error_text(tmp_path, ok_script):
    manager = _make_manager(tmp_path, ok_script)
    store = NamedThemesStore(global_dir=tmp_path)
    host = _StubApp()
    async with host.run_test():
        host._active_theme_extra_css = ""
        tools = build_orchestrator_tools(
            manager, themes_store=store, app=host,
        )
        out = await tools["set_theme"]({"spec": {"bogus": True}})
        assert "invalid" in out["content"][0]["text"].lower()


@pytest.mark.asyncio
async def test_save_theme_with_explicit_spec_persists(tmp_path, ok_script):
    manager = _make_manager(tmp_path, ok_script)
    store = NamedThemesStore(global_dir=tmp_path)
    host = _StubApp()
    async with host.run_test():
        host._active_theme_extra_css = ""
        tools = build_orchestrator_tools(
            manager, themes_store=store, app=host,
        )
        out = await tools["save_theme"](
            {"name": "alpha", "spec": _spec_dict("#445566")}
        )
        assert "saved" in out["content"][0]["text"].lower()
        assert store.load("alpha").palette.primary == "#445566"


@pytest.mark.asyncio
async def test_save_theme_without_spec_snapshots_active(tmp_path, ok_script):
    """save_theme with no spec must read the live palette + cached extra_css."""
    manager = _make_manager(tmp_path, ok_script)
    store = NamedThemesStore(global_dir=tmp_path)
    host = _StubApp()
    async with host.run_test():
        host._active_theme_extra_css = "X { color: red; }"
        tools = build_orchestrator_tools(
            manager, themes_store=store, app=host,
        )
        out = await tools["save_theme"]({"name": "snapshot"})
        assert "saved" in out["content"][0]["text"].lower()
        loaded = store.load("snapshot")
        assert loaded is not None
        assert loaded.extra_css == "X { color: red; }"
        # Palette mirrors host.current_theme (whatever Textual default is).
        assert loaded.palette.primary  # non-empty


@pytest.mark.asyncio
async def test_load_theme_applies_saved(tmp_path, ok_script):
    manager = _make_manager(tmp_path, ok_script)
    store = NamedThemesStore(global_dir=tmp_path)
    store.save(
        "alpha",
        ThemeSpec(palette=ThemePalette(primary="#998877")),
    )
    host = _StubApp()
    async with host.run_test():
        host._active_theme_extra_css = ""
        tools = build_orchestrator_tools(
            manager, themes_store=store, app=host,
        )
        out = await tools["load_theme"]({"name": "alpha", "persist": False})
        assert "loaded" in out["content"][0]["text"].lower()
        assert host.theme == "patchfeld:alpha"


@pytest.mark.asyncio
async def test_load_theme_falls_through_to_builtin(tmp_path, ok_script):
    manager = _make_manager(tmp_path, ok_script)
    store = NamedThemesStore(global_dir=tmp_path)
    host = _StubApp()
    async with host.run_test():
        host._active_theme_extra_css = ""
        tools = build_orchestrator_tools(
            manager, themes_store=store, app=host,
        )
        out = await tools["load_theme"]({"name": "nord", "persist": False})
        text = out["content"][0]["text"].lower()
        assert "loaded" in text
        assert host.theme == "nord"


@pytest.mark.asyncio
async def test_load_theme_unknown_name_returns_error(tmp_path, ok_script):
    manager = _make_manager(tmp_path, ok_script)
    store = NamedThemesStore(global_dir=tmp_path)
    host = _StubApp()
    async with host.run_test():
        host._active_theme_extra_css = ""
        tools = build_orchestrator_tools(
            manager, themes_store=store, app=host,
        )
        out = await tools["load_theme"](
            {"name": "no-such-theme-anywhere", "persist": False}
        )
        text = out["content"][0]["text"].lower()
        assert "not found" in text or "unknown" in text


@pytest.mark.asyncio
async def test_list_themes_returns_saved_and_builtin(tmp_path, ok_script):
    manager = _make_manager(tmp_path, ok_script)
    store = NamedThemesStore(global_dir=tmp_path)
    store.save("alpha", ThemeSpec(palette=ThemePalette(primary="#aabbcc")))
    host = _StubApp()
    async with host.run_test():
        host._active_theme_extra_css = ""
        tools = build_orchestrator_tools(
            manager, themes_store=store, app=host,
        )
        out = await tools["list_themes"]({})
        payload = json.loads(out["content"][0]["text"])
        assert "alpha" in payload["saved"]
        assert "nord" in payload["builtin"]  # built-in always present
        assert "active" in payload


@pytest.mark.asyncio
async def test_get_theme_with_name_returns_saved(tmp_path, ok_script):
    manager = _make_manager(tmp_path, ok_script)
    store = NamedThemesStore(global_dir=tmp_path)
    store.save("alpha", ThemeSpec(palette=ThemePalette(primary="#aabbcc")))
    host = _StubApp()
    async with host.run_test():
        host._active_theme_extra_css = ""
        tools = build_orchestrator_tools(
            manager, themes_store=store, app=host,
        )
        out = await tools["get_theme"]({"name": "alpha"})
        payload = json.loads(out["content"][0]["text"])
        assert payload["palette"]["primary"] == "#aabbcc"


@pytest.mark.asyncio
async def test_get_theme_no_name_returns_active(tmp_path, ok_script):
    manager = _make_manager(tmp_path, ok_script)
    store = NamedThemesStore(global_dir=tmp_path)
    host = _StubApp()
    async with host.run_test():
        host._active_theme_extra_css = "X { color: red; }"
        tools = build_orchestrator_tools(
            manager, themes_store=store, app=host,
        )
        out = await tools["get_theme"]({})
        payload = json.loads(out["content"][0]["text"])
        assert "name" in payload
        assert "palette" in payload
        assert payload["extra_css"] == "X { color: red; }"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_orchestrator_tools_theme.py -v`
Expected: FAIL — `themes_store` kwarg is unknown to `build_orchestrator_tools`.

- [ ] **Step 3: Add the 5 handler builders to `patchfeld/orchestrator/tools.py`**

Open `patchfeld/orchestrator/tools.py`. Add this import near the existing imports:

```python
from patchfeld.persistence.themes_store import NamedThemesStore
from patchfeld.theme.engine import apply_theme
from patchfeld.theme.spec import ThemePalette, ThemeSpec
```

Add helper at module level (just below `_get_layout_handler` is fine):

```python
def _palette_from_textual_theme(textual_theme) -> ThemePalette:
    """Snapshot a live textual.theme.Theme into our ThemePalette."""
    return ThemePalette(
        primary=textual_theme.primary,
        secondary=textual_theme.secondary,
        warning=textual_theme.warning,
        error=textual_theme.error,
        success=textual_theme.success,
        accent=textual_theme.accent,
        foreground=textual_theme.foreground,
        background=textual_theme.background,
        surface=textual_theme.surface,
        panel=textual_theme.panel,
        boost=textual_theme.boost,
        dark=textual_theme.dark,
        luminosity_spread=textual_theme.luminosity_spread,
        text_alpha=textual_theme.text_alpha,
        variables=dict(textual_theme.variables),
    )
```

Now add the five handlers (place these alongside the existing `_set_layout_handler` block):

```python
def _set_theme_handler(themes_store: NamedThemesStore, app):
    async def set_theme_tool(args: dict) -> dict:
        try:
            spec = ThemeSpec.model_validate(args["spec"])
        except Exception as e:
            return {"content": [{"type": "text", "text": f"Invalid ThemeSpec: {e}"}]}
        try:
            await apply_theme(app, spec, theme_name="<inline>")
        except Exception as e:
            return {"content": [{"type": "text", "text": f"Apply error: {e}"}]}
        return {"content": [{"type": "text", "text": "Theme applied."}]}
    return set_theme_tool


def _save_theme_handler(themes_store: NamedThemesStore, app):
    async def save_theme_tool(args: dict) -> dict:
        name = args["name"]
        if "spec" in args:
            try:
                spec = ThemeSpec.model_validate(args["spec"])
            except Exception as e:
                return {"content": [{"type": "text", "text": f"Invalid ThemeSpec: {e}"}]}
        else:
            try:
                palette = _palette_from_textual_theme(app.current_theme)
            except Exception as e:
                return {"content": [{"type": "text",
                                     "text": f"Could not snapshot active theme: {e}"}]}
            extra = getattr(app, "_active_theme_extra_css", "") or ""
            spec = ThemeSpec(palette=palette, extra_css=extra)
        try:
            themes_store.save(name, spec)
        except ValueError as e:
            return {"content": [{"type": "text", "text": f"Invalid theme name: {e}"}]}
        return {"content": [{"type": "text", "text": f"Saved theme {name!r}."}]}
    return save_theme_tool


def _load_theme_handler(themes_store: NamedThemesStore, app, config_store=None):
    async def load_theme_tool(args: dict) -> dict:
        name = args["name"]
        persist = bool(args.get("persist", True))
        scope = args.get("scope", "global")
        if scope not in ("global", "project"):
            return {"content": [{"type": "text",
                                 "text": f"Invalid scope: {scope!r} (use 'global' or 'project')"}]}
        # 1. Try saved store.
        spec = themes_store.load(name)
        if spec is not None:
            try:
                await apply_theme(app, spec, theme_name=name)
            except Exception as e:
                return {"content": [{"type": "text", "text": f"Apply error: {e}"}]}
        else:
            # 2. Fall through to Textual built-ins.
            try:
                available = app.available_themes
            except Exception:
                available = {}
            if name not in available:
                return {"content": [{"type": "text", "text": f"Theme not found: {name}"}]}
            # Built-in pass-through: clear our extra_css source, set theme directly.
            from patchfeld.theme.engine import _EXTRA_CSS_KEY
            if _EXTRA_CSS_KEY in app.stylesheet.source:
                del app.stylesheet.source[_EXTRA_CSS_KEY]
            app._active_theme_extra_css = ""
            app.theme = name
            try:
                app.refresh_css()
            except Exception:
                pass

        # 3. Persist active-theme pointer if asked.
        if persist:
            if scope == "global" and config_store is not None:
                cfg = config_store.load()
                cfg.ui.active_theme = name
                config_store.save(cfg)
            elif scope == "project" and getattr(app, "_workspace", None) is not None:
                from patchfeld.persistence.workspace_store import save_workspace
                ws = app._workspace.model_copy(update={"active_theme": name})
                app._workspace = ws
                save_workspace(app.cwd, ws)

        return {"content": [{"type": "text", "text": f"Loaded theme {name!r}."}]}
    return load_theme_tool


def _list_themes_handler(themes_store: NamedThemesStore, app):
    async def list_themes_tool(_args: dict) -> dict:
        saved = themes_store.list()
        try:
            builtin = sorted(
                n for n in app.available_themes.keys()
                if not n.startswith("patchfeld:")
            )
        except Exception:
            builtin = []
        active = getattr(app, "theme", None) or ""
        # Strip the "patchfeld:" prefix from active for user-facing display
        # so a saved theme named "alpha" reads back as "alpha".
        if active.startswith("patchfeld:"):
            active_display = active[len("patchfeld:"):]
        else:
            active_display = active
        payload = {"saved": saved, "builtin": builtin, "active": active_display}
        return {"content": [{"type": "text", "text": json.dumps(payload)}]}
    return list_themes_tool


def _get_theme_handler(themes_store: NamedThemesStore, app):
    async def get_theme_tool(args: dict) -> dict:
        name = (args or {}).get("name")
        if name:
            spec = themes_store.load(name)
            if spec is None:
                return {"content": [{"type": "text", "text": f"Theme not found: {name}"}]}
            return {"content": [{"type": "text", "text": json.dumps(spec.model_dump(mode="json"))}]}
        # No name → snapshot the active theme.
        try:
            palette = _palette_from_textual_theme(app.current_theme).model_dump(mode="json")
        except Exception as e:
            return {"content": [{"type": "text", "text": f"Cannot read active theme: {e}"}]}
        active = getattr(app, "theme", "") or ""
        if active.startswith("patchfeld:"):
            active = active[len("patchfeld:"):]
        extra = getattr(app, "_active_theme_extra_css", "") or ""
        payload = {"name": active, "palette": palette, "extra_css": extra}
        return {"content": [{"type": "text", "text": json.dumps(payload)}]}
    return get_theme_tool
```

- [ ] **Step 4: Wire the new handlers into `build_orchestrator_tools`**

In `build_orchestrator_tools` (`patchfeld/orchestrator/tools.py:401`), add a `themes_store` kwarg and a new gating block. The full updated signature:

```python
def build_orchestrator_tools(
    manager: AgentManager,
    *,
    apply_layout=None,
    layouts_store: NamedLayoutsStore | None = None,
    themes_store: NamedThemesStore | None = None,
    config_store: ConfigStore | None = None,
    actions: ActionRegistry | None = None,
    rebind_keys=None,
    widget_registry: WidgetRegistry | None = None,
    current_layout=None,
    app=None,
):
```

Add this block alongside the existing layout block (e.g. after the `if widget_registry is not None and current_layout is not None:` block, before the tabs block):

```python
    if themes_store is not None and app is not None:
        handlers["set_theme"] = _set_theme_handler(themes_store, app)
        handlers["save_theme"] = _save_theme_handler(themes_store, app)
        handlers["load_theme"] = _load_theme_handler(
            themes_store, app, config_store=config_store,
        )
        handlers["list_themes"] = _list_themes_handler(themes_store, app)
        handlers["get_theme"] = _get_theme_handler(themes_store, app)
```

- [ ] **Step 5: Run handler tests to verify they pass**

Run: `uv run pytest tests/test_orchestrator_tools_theme.py -v`
Expected: PASS, all 10 tests green.

If `test_save_theme_without_spec_snapshots_active` fails because `app.current_theme` returns `None` on a freshly-mounted host App, switch to `app.themes[app.theme]` or whatever `available_themes[app.theme]` returns; the goal is to extract the active palette. Confirm what `app.current_theme` is by adding a `print(host.current_theme)` and re-running — adjust the snapshot helper accordingly.

- [ ] **Step 6: Wire the new handlers into `build_orchestrator_mcp_server`**

In `build_orchestrator_mcp_server` (`patchfeld/orchestrator/tools.py:452`), add the same `themes_store` kwarg and an `if themes_store is not None and app is not None:` block that registers each tool with `tool(name, desc, schema)(handler)`. Use these descriptions:

```python
    if themes_store is not None and app is not None:
        theme_specs = [
            (
                "set_theme",
                "Apply a ThemeSpec to the live app. The spec is "
                "{ palette: {primary, secondary, warning, error, success, accent, "
                "foreground, background, surface, panel, boost, dark, "
                "luminosity_spread, text_alpha, variables}, extra_css: str }. "
                "Color strings follow Textual's syntax (#rrggbb or named). "
                "If `extra_css` is present, it is parsed at app scope; bad "
                "CSS is rejected before the palette change. Only ship "
                "`extra_css` you have personally authored — CSS can hide "
                "chrome, fake widgets, or break input visibility. Does NOT "
                "persist; use save_theme + load_theme for that.",
                {"spec": dict},
                _set_theme_handler(themes_store, app),
            ),
            (
                "save_theme",
                "Save a ThemeSpec to ~/.config/patchfeld/themes/<name>.json. "
                "If `spec` is omitted, snapshots the currently-active palette "
                "and the last applied extra_css. Use this to capture the "
                "live look as a named theme.",
                {"name": str, "spec": dict},
                _save_theme_handler(themes_store, app),
            ),
            (
                "load_theme",
                "Load a saved theme by name and apply it. Falls through to "
                "Textual built-ins (textual-dark, nord, gruvbox, dracula, "
                "catppuccin-*, …) if the name is not in the saved store. "
                "When `persist` (default true) the active-theme pointer is "
                "written: `scope='global'` writes ~/.config/patchfeld/config.toml "
                "ui.active_theme; `scope='project'` writes workspace.json's "
                "active_theme. Default scope is 'global'.",
                {"name": str, "persist": bool, "scope": str},
                _load_theme_handler(themes_store, app, config_store=config_store),
            ),
            (
                "list_themes",
                "Return {saved, builtin, active}. `saved` is the user's "
                "named themes; `builtin` is Textual's built-in themes "
                "(read-only); `active` is the current theme name (without "
                "the internal patchfeld: prefix).",
                {},
                _list_themes_handler(themes_store, app),
            ),
            (
                "get_theme",
                "Return a saved theme's full spec when `name` is given. "
                "Without `name`, returns the active theme as "
                "{name, palette, extra_css}. Pass the result back through "
                "set_theme to apply edits.",
                {"name": str},
                _get_theme_handler(themes_store, app),
            ),
        ]
        for name, desc, schema, handler in theme_specs:
            sdk_tools.append(tool(name, desc, schema)(handler))
```

- [ ] **Step 7: Run all orchestrator tool tests**

Run: `uv run pytest tests/test_orchestrator_tools.py tests/test_orchestrator_tools_layout.py tests/test_orchestrator_tools_theme.py tests/test_orchestrator_tools_config.py -v`
Expected: PASS, no regressions.

- [ ] **Step 8: Commit**

```bash
git add patchfeld/orchestrator/tools.py tests/test_orchestrator_tools_theme.py
git commit -m "feat(orchestrator): set/save/load/list/get_theme MCP tools"
```

---

## Task 8: Wire `themes_store` through `OrchestratorSession`

**Files:**
- Modify: `patchfeld/orchestrator/session.py:60-89` (the `__init__` and `_build_and_start_inner`)

- [ ] **Step 1: Add `themes_store` kwarg to `OrchestratorSession.__init__`**

In `patchfeld/orchestrator/session.py`, modify the `__init__` signature and store the new kwarg:

```python
    def __init__(
        self,
        *,
        cwd: Path,
        bus: EventBus,
        manager: AgentManager,
        adapter: SDKAdapter | None = None,
        model: str | None = None,
        apply_layout=None,
        layouts_store=None,
        themes_store=None,
        config_store=None,
        actions=None,
        rebind_keys=None,
        widget_registry=None,
        current_layout=None,
        app=None,
    ) -> None:
```

Inside the body, after `self._layouts_store = layouts_store`, add:

```python
        self._themes_store = themes_store
```

- [ ] **Step 2: Forward to `build_orchestrator_mcp_server`**

In `_build_and_start_inner` (`session.py:162`), update the call:

```python
        mcp_server = build_orchestrator_mcp_server(
            self._manager,
            apply_layout=self._apply_layout,
            layouts_store=self._layouts_store,
            themes_store=self._themes_store,
            config_store=self._config_store,
            actions=self._actions,
            rebind_keys=self._rebind_keys,
            widget_registry=self._widget_registry,
            current_layout=self._current_layout,
            app=self._app,
        )
```

- [ ] **Step 3: Run orchestrator session tests**

Run: `uv run pytest tests/test_orchestrator_session.py tests/test_orchestrator_tools.py -v`
Expected: PASS — kwarg is optional and defaults to None, so no existing tests break.

- [ ] **Step 4: Commit**

```bash
git add patchfeld/orchestrator/session.py
git commit -m "chore(orchestrator): forward themes_store kwarg to MCP build"
```

---

## Task 9: Theme switcher modal

**Files:**
- Create: `patchfeld/widgets/theme_switcher.py`
- Test: `tests/test_theme_switcher.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_theme_switcher.py`:

```python
import pytest
from textual.app import App
from textual.widgets import ListView

from patchfeld.persistence.themes_store import NamedThemesStore
from patchfeld.theme.spec import ThemePalette, ThemeSpec
from patchfeld.widgets.theme_switcher import ThemeSwitcherScreen


def _spec() -> ThemeSpec:
    return ThemeSpec(palette=ThemePalette(primary="#005577"))


@pytest.mark.asyncio
async def test_switcher_lists_saved_first_then_builtins(tmp_path):
    store = NamedThemesStore(global_dir=tmp_path)
    store.save("alpha", _spec())
    store.save("beta", _spec())

    selected: list[str | None] = []

    class _Host(App):
        async def on_mount(self):
            screen = ThemeSwitcherScreen(
                store=store,
                available_builtins=["nord", "gruvbox"],
                active="alpha",
            )
            await self.push_screen(screen, selected.append)

    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        list_view = screen.query_one(ListView)
        names = [item.name for item in list_view.children if item.name]
        # Saved come first
        assert names[:2] == ["alpha", "beta"]
        # Built-ins after
        assert "nord" in names
        assert "gruvbox" in names


@pytest.mark.asyncio
async def test_switcher_marks_active_theme(tmp_path):
    store = NamedThemesStore(global_dir=tmp_path)
    store.save("alpha", _spec())

    rendered_labels: list[str] = []

    class _Host(App):
        async def on_mount(self):
            screen = ThemeSwitcherScreen(
                store=store,
                available_builtins=["nord"],
                active="alpha",
            )
            await self.push_screen(screen, lambda _: None)

    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        # Render the labels to look for the active marker.
        for item in screen.query_one(ListView).children:
            for child in item.walk_children():
                text = getattr(child, "renderable", None)
                if text:
                    rendered_labels.append(str(text))

    joined = " ".join(rendered_labels)
    assert "* alpha" in joined or "*alpha" in joined


@pytest.mark.asyncio
async def test_switcher_dismisses_with_name_on_select(tmp_path):
    store = NamedThemesStore(global_dir=tmp_path)
    store.save("alpha", _spec())

    selected: list[str | None] = []

    class _Host(App):
        async def on_mount(self):
            screen = ThemeSwitcherScreen(
                store=store,
                available_builtins=[],
                active="alpha",
            )
            await self.push_screen(screen, selected.append)

    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.screen.dismiss("alpha")
        await pilot.pause()

    assert selected == ["alpha"]


@pytest.mark.asyncio
async def test_switcher_dismisses_with_none_on_escape(tmp_path):
    store = NamedThemesStore(global_dir=tmp_path)
    store.save("alpha", _spec())

    selected: list[str | None] = []

    class _Host(App):
        async def on_mount(self):
            await self.push_screen(
                ThemeSwitcherScreen(
                    store=store, available_builtins=[], active="alpha",
                ),
                selected.append,
            )

    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()

    assert selected == [None]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_theme_switcher.py -v`
Expected: FAIL with import error.

- [ ] **Step 3: Implement `patchfeld/widgets/theme_switcher.py`**

```python
from textual.binding import Binding
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import Footer, Label, ListItem, ListView

from patchfeld.persistence.themes_store import NamedThemesStore


class ThemeSwitcherScreen(ModalScreen[str | None]):
    """Pick a theme. Esc dismisses with None; selecting dismisses with the name."""

    DEFAULT_CSS = """
    ThemeSwitcherScreen {
        align: center middle;
    }
    ThemeSwitcherScreen > Container {
        width: 50%;
        height: 60%;
        border: thick $primary;
        background: $surface;
        padding: 1 2;
    }
    ThemeSwitcherScreen ListView {
        height: 1fr;
    }
    """

    BINDINGS = [Binding("escape", "dismiss_none", "cancel")]

    def __init__(
        self,
        *,
        store: NamedThemesStore,
        available_builtins: list[str],
        active: str,
    ) -> None:
        super().__init__()
        self._store = store
        self._builtins = list(available_builtins)
        self._active = active

    def compose(self):
        items: list[ListItem] = []
        for name in self._store.list():
            label = f"* {name}" if name == self._active else f"  {name}"
            items.append(ListItem(Label(label), name=name))
        if self._builtins:
            items.append(ListItem(Label("─ built-ins ─"), name=None))
        for name in self._builtins:
            label = f"* {name}" if name == self._active else f"  {name}"
            items.append(ListItem(Label(label), name=name))
        with Container():
            yield Label("Load theme:")
            yield ListView(*items)
            yield Footer()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.item.name is None:
            return  # separator row — ignore
        self.dismiss(event.item.name)

    def action_dismiss_none(self) -> None:
        self.dismiss(None)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_theme_switcher.py -v`
Expected: PASS, all 4 tests green.

If `test_switcher_marks_active_theme` fails because the label-walking technique misses the active marker, simplify to assert `screen.query_one(ListView).children[0].name == "alpha"` and that the rendered text contains an asterisk somewhere. The exact rendering mechanism is less important than the data — fix the assertion to whatever cleanly inspects the data.

- [ ] **Step 5: Commit**

```bash
git add patchfeld/widgets/theme_switcher.py tests/test_theme_switcher.py
git commit -m "feat(widgets): ThemeSwitcherScreen modal mirroring layout switcher"
```

---

## Task 10: App boot wiring — store, seed, resolve, apply, action, key

**Files:**
- Modify: `patchfeld/app.py` (multiple locations: imports, `__init__`, `_register_actions`, `BINDINGS`, `action_show_help`, `on_mount`, plus a new helper `_apply_theme_by_name`)

- [ ] **Step 1: Add imports**

In `patchfeld/app.py`, add to the imports section (alongside the existing `from patchfeld.persistence.layouts_store import NamedLayoutsStore`):

```python
from patchfeld.persistence.themes_store import NamedThemesStore
from patchfeld.theme.engine import _EXTRA_CSS_KEY, apply_theme
from patchfeld.theme.spec import ThemePalette, ThemeSpec
from patchfeld.widgets.theme_switcher import ThemeSwitcherScreen
```

- [ ] **Step 2: Construct the themes store and forward to OrchestratorSession**

In `PatchfeldApp.__init__` (`app.py:153`), after `self.layouts_store = NamedLayoutsStore(global_dir=self._global_dir)` (line 172), add:

```python
        self.themes_store = NamedThemesStore(global_dir=self._global_dir)
```

Update the `OrchestratorSession(...)` construction (around line 180) to pass it:

```python
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
        )
```

- [ ] **Step 3: Add `ctrl+shift+l` binding**

In the `BINDINGS` list (`app.py:134`), add a new binding right after `Binding("ctrl+l", "open_layout_switcher", "layouts")`:

```python
        Binding("ctrl+shift+l", "open_theme_switcher", "themes"),
```

- [ ] **Step 4: Register the `open_theme_switcher` action**

In `_register_actions` (`app.py:198`), after the existing `open_layout_switcher` registration (line 230), add:

```python
        self.actions_registry.register(
            "open_theme_switcher", self.action_open_theme_switcher,
            description="Open the saved-themes switcher modal.", args_schema={},
        )
```

- [ ] **Step 5: Implement `action_open_theme_switcher` and `_apply_theme_by_name`**

Add these methods to `PatchfeldApp` (anywhere in the class — adjacent to `action_open_layout_switcher` is natural):

```python
    def action_open_theme_switcher(self) -> None:
        import asyncio as _asyncio

        try:
            builtins = sorted(
                n for n in self.available_themes.keys()
                if not n.startswith("patchfeld:")
            )
        except Exception:
            builtins = []
        active = self.theme or ""
        if active.startswith("patchfeld:"):
            active = active[len("patchfeld:"):]

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
            from patchfeld.persistence.workspace_store import save_workspace
            save_workspace(self.cwd, ws)
```

- [ ] **Step 6: Update `action_show_help`**

In `action_show_help` (`app.py:400`), update the message to mention `ctrl+shift+l themes`:

```python
    def action_show_help(self) -> None:
        self.notify(
            "/ command bar · ctrl-q quit · ctrl-h history · ctrl-l layouts · "
            "ctrl-shift-l themes · "
            "ctrl-pgup/pgdn prev/next tab · ctrl-1..9 tab N · ctrl-t new tab · "
            "ctrl-w close tab · /reset new · /resume past · /rename title · ? help",
            title="keybindings",
        )
```

- [ ] **Step 7: Add boot-time seed and apply in `on_mount`**

In `on_mount` (`app.py:595`), modify so that AFTER the existing `if self.layouts_store.load("default") is None:` block (around line 602) AND AFTER `await self.orchestrator.start()`, the workspace is loaded first (so `self._workspace` is available for project-scope resolution), then seed and apply theme. Move blocks if needed. The desired order:

```python
    async def on_mount(self) -> None:
        self._rebind_keys()
        # Layout default seed (existing behavior)
        if self.layouts_store.load("default") is None:
            self.layouts_store.save("default", dashboard_layout())
        await self.orchestrator.start()
        self.event_bus.subscribe(OpenResumePicker, self._on_open_resume_picker)
        ws = self._load_or_seed_workspace()
        self._workspace = ws
        self._active_tab_id = ws.active
        await self._mount_workspace(ws)
        save_local_workspace(self.cwd, ws)

        # Theme seed: snapshot the current Textual theme as "default" if not present.
        if self.themes_store.load("default") is None:
            try:
                from patchfeld.orchestrator.tools import _palette_from_textual_theme
                pal = _palette_from_textual_theme(self.current_theme)
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
```

(Note: `_palette_from_textual_theme` was added to `patchfeld/orchestrator/tools.py` in Task 7. Importing from there is fine; alternatively move it to `patchfeld/theme/engine.py` if you prefer no orchestrator-tools import inside `app.py`.)

- [ ] **Step 8: Run app smoke tests**

Run: `uv run pytest tests/test_app_smoke.py tests/test_app_smoke_tabs.py -v`
Expected: PASS — boot still succeeds; the new theme apply is best-effort.

If a smoke test fails because `self.current_theme` returns `None` early in mount, move the seed step inside a `call_after_refresh` callback or read after a `pilot.pause()`. Adjust until smoke tests pass.

- [ ] **Step 9: Commit**

```bash
git add patchfeld/app.py
git commit -m "feat(app): construct themes store, seed default, apply on boot, ctrl+shift+l switcher"
```

---

## Task 11: App-level smoke test (boot scenarios)

**Files:**
- Create: `tests/test_app_smoke_theme.py`

- [ ] **Step 1: Write smoke tests for boot scenarios**

Create `tests/test_app_smoke_theme.py`:

```python
import json
from pathlib import Path

import pytest

from patchfeld.app import PatchfeldApp
from patchfeld.config import ConfigStore
from patchfeld.persistence.paths import project_workspace_path
from patchfeld.persistence.themes_store import NamedThemesStore
from patchfeld.theme.spec import ThemePalette, ThemeSpec


@pytest.mark.asyncio
async def test_boot_seeds_default_theme(tmp_path: Path):
    """First-run boot writes a 'default' theme to ~/.config/patchfeld/themes/."""
    global_dir = tmp_path / "config"
    cwd = tmp_path / "project"
    cwd.mkdir()

    app = PatchfeldApp(cwd=cwd, global_dir=global_dir)
    async with app.run_test() as pilot:
        await pilot.pause()

    store = NamedThemesStore(global_dir=global_dir)
    assert "default" in store.list()
    spec = store.load("default")
    assert spec is not None
    assert spec.palette.primary  # has SOMETHING


@pytest.mark.asyncio
async def test_boot_does_not_overwrite_existing_default(tmp_path: Path):
    """If user has saved their own 'default', boot must not clobber it."""
    global_dir = tmp_path / "config"
    cwd = tmp_path / "project"
    cwd.mkdir()
    seed_store = NamedThemesStore(global_dir=global_dir)
    seed_store.save(
        "default",
        ThemeSpec(palette=ThemePalette(primary="#deadbe")),
    )

    app = PatchfeldApp(cwd=cwd, global_dir=global_dir)
    async with app.run_test() as pilot:
        await pilot.pause()

    after = seed_store.load("default")
    assert after.palette.primary == "#deadbe"


@pytest.mark.asyncio
async def test_boot_with_workspace_active_theme_applies_builtin(tmp_path: Path):
    global_dir = tmp_path / "config"
    cwd = tmp_path / "project"
    cwd.mkdir()
    # Pre-seed workspace.json with active_theme="nord".
    project_state = cwd / ".patchfeld"
    project_state.mkdir()
    (project_state / "workspace.json").write_text(json.dumps({
        "version": 1,
        "tabs": [
            {"id": "default", "title": "default", "layout": {
                "version": 1,
                "layout": {"id": "orch", "widget": "OrchestratorChat"},
            }},
        ],
        "active": "default",
        "active_theme": "nord",
    }))

    app = PatchfeldApp(cwd=cwd, global_dir=global_dir)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.theme == "nord"


@pytest.mark.asyncio
async def test_boot_with_global_active_theme_applies_builtin(tmp_path: Path):
    global_dir = tmp_path / "config"
    cwd = tmp_path / "project"
    cwd.mkdir()
    # Pre-seed config.toml with ui.active_theme="gruvbox".
    cfg_store = ConfigStore(global_dir=global_dir)
    cfg = cfg_store.load()
    cfg.ui.active_theme = "gruvbox"
    cfg_store.save(cfg)

    app = PatchfeldApp(cwd=cwd, global_dir=global_dir)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.theme == "gruvbox"


@pytest.mark.asyncio
async def test_boot_with_corrupted_active_theme_falls_back(tmp_path: Path):
    """Active theme that doesn't exist anywhere should not crash boot."""
    global_dir = tmp_path / "config"
    cwd = tmp_path / "project"
    cwd.mkdir()
    cfg_store = ConfigStore(global_dir=global_dir)
    cfg = cfg_store.load()
    cfg.ui.active_theme = "no-such-theme-xyz"
    cfg_store.save(cfg)

    app = PatchfeldApp(cwd=cwd, global_dir=global_dir)
    async with app.run_test() as pilot:
        await pilot.pause()
        # App is alive; theme was either left at Textual default or
        # fell back to default. We just need it not to crash.
        assert app.theme is not None
```

- [ ] **Step 2: Run the smoke tests**

Run: `uv run pytest tests/test_app_smoke_theme.py -v`
Expected: PASS, all 5 tests green.

If `test_boot_with_workspace_active_theme_applies_builtin` fails because the workspace.json is rejected for missing OrchestratorChat in the only tab — verify the JSON has `OrchestratorChat` in the seeded layout (the snippet above does). If still failing, investigate `_load_or_seed_workspace` to see whether it actually read the file you wrote.

- [ ] **Step 3: Run the full test suite to catch any cross-cutting regressions**

Run: `uv run pytest -x`
Expected: PASS — full suite green.

If anything fails, fix the regression before committing.

- [ ] **Step 4: Commit**

```bash
git add tests/test_app_smoke_theme.py
git commit -m "test(app): boot scenarios for theme seed/resolve/fallback"
```

---

## Task 12: End-to-end loop (manual verification)

This task is not test-driven — it's a sanity check. The unit + smoke tests are the green-light; this is just a "did anything obvious break visually."

- [ ] **Step 1: Boot the app in a scratch dir**

```bash
cd /tmp && mkdir -p patchfeld_smoke && cd patchfeld_smoke
uv --project /Users/jimmy.mills/Developer/patchfeld run python -m patchfeld
```

(If the launcher invocation differs, check `patchfeld/__main__.py` for the right entry point.)

- [ ] **Step 2: Verify visual baseline matches before/after**

The seeded `default` theme captures the Textual default palette. Boot should look identical to before this branch landed.

- [ ] **Step 3: Open the theme switcher**

Press `ctrl+shift+l`. The modal should list `default` (with `*`) and the 21 built-in Textual themes.

- [ ] **Step 4: Switch to `nord`**

Select `nord` and press Enter. Colors should change. The `* default` marker is no longer in front of default; instead `* nord` is the marked entry on next open.

- [ ] **Step 5: Restart the app**

Quit (`ctrl+q`) and re-run the same launcher. The theme should still be `nord` (because the modal applies `persist=true, scope="global"`).

- [ ] **Step 6: Recover to default**

Press `ctrl+shift+l`, select `default`. Restart. Still `default`. Done.

- [ ] **Step 7: Tag a working state**

```bash
git tag wip/theme-system-manually-verified
```

(Tag is local-only; user can drop or push as they like.)

---

## Self-review notes

Spec coverage check:
- §3 ThemeSpec → Task 1 ✓
- §3 NamedThemesStore → Task 2 ✓
- §3 Config replacement → Task 3 ✓
- §3 Workspace.active_theme → Task 4 ✓
- §3 apply_theme engine → Task 5 ✓
- §3 `_active_theme_extra_css` cache init → Task 6 ✓
- §4 set/save/load/list/get_theme tools → Task 7 ✓
- §4 wiring through OrchestratorSession → Task 8 ✓
- §5 ThemeSwitcherScreen → Task 9 ✓
- §6 boot wiring (store, seed, resolve, apply, action, key, help) → Task 10 ✓
- §7 tests — split across all task TDD steps + dedicated smoke task → Task 11 ✓
- Manual sanity check → Task 12 ✓

Type/name consistency:
- `_EXTRA_CSS_KEY = ("patchfeld_theme", "extra_css")` is defined in Task 5 (engine), referenced in Task 7 (tool), Task 10 (app helper). ✓
- `_palette_from_textual_theme` defined in Task 7 (tools.py), reused in Task 10 (`on_mount`). ✓
- `_apply_theme_by_name(name, *, persist=False, scope="global")` signature consistent across Task 10 (defined) and Task 7's `load_theme` handler (which inlines its own resolution rather than calling the helper, to avoid orchestrator → app coupling). ✓
- `themes_store` kwarg name consistent across Task 7 (`build_orchestrator_tools`, `build_orchestrator_mcp_server`), Task 8 (`OrchestratorSession.__init__`), Task 10 (`PatchfeldApp.__init__`). ✓
