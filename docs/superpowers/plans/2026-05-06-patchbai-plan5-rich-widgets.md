# Patchbai Plan 5 — Rich Widget Library

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the seven widgets the spec promised — `Markdown`, `FileViewer`, `FileTree`, `DiffViewer`, `LogTail`, `Notebook`, `Terminal` — register them all in the default registry, expose a `list_widgets` MCP tool so the orchestrator can discover them, and clean up the three plan-4 prep items the final reviewer flagged (orchestrator-tools-as-dict, WeakKeyDictionary for `_last_applied_spec`, generalized `Config.set_path`). After this plan the orchestrator can ask the user "want me to mount a diff viewer for that change?" and reshape the layout to include any of the seven new widgets.

**Architecture:** Most widgets thinly wrap Textual built-ins (`Markdown`, `DirectoryTree`, `TextArea`) with our `Container` shell + props normalization. Three widgets are net-new: `DiffViewer` (renders a unified diff via `rich.syntax`), `LogTail` (polls a file with a 250ms reactive timer), and `Notebook` (`TextArea` over a persisted file in `<cwd>/.patchbai/scratch/<name>.md`). `Terminal` depends on `textual-terminal` if it installs cleanly against Textual 8.2.5; if not, the task is documented as BLOCKED and the rest of the plan ships without it. `WidgetRegistry.register` grows an optional `description` and `props_schema` so `list_widgets` can return enough for the orchestrator AI to pick widgets and pass props correctly.

**Tech Stack:** Python 3.11+, Textual, pydantic v2, `claude-agent-sdk`, `tomli-w`, **NEW:** `textual-terminal` (gated on Task 12), pytest + pytest-asyncio.

**Non-goals for this plan (deferred to plan 6):**
- Mode-C custom widgets — `register_custom_widget(name, source)` exec sandbox
- True incremental widget reuse across `set_layout` (props-only updates without re-mount). Plan 4's idempotent fast-path stays; full prop-diff is plan 6 if at all.
- Replacing `bypassPermissions` with a Textual approval modal
- Peer-to-peer messaging between child agents

---

## File Structure

```
patchbai/
  layout/
    engine.py                   (MODIFY: WeakKeyDictionary for _last_applied_spec)
    registry.py                 (MODIFY: register accepts description + props_schema)
  config.py                     (MODIFY: generic set_path/get_path over all sections)
  orchestrator/
    tools.py                    (MODIFY: build_orchestrator_tools returns dict; add list_widgets)
  widgets/
    markdown.py                 (NEW: thin wrapper around textual.widgets.Markdown)
    file_viewer.py              (NEW: TextArea read_only + syntax detection)
    file_tree.py                (NEW: thin wrapper around DirectoryTree)
    diff_viewer.py              (NEW: rich.syntax-based unified diff renderer)
    log_tail.py                 (NEW: file-tailing widget with 250ms poll)
    notebook.py                 (NEW: TextArea persisted to .patchbai/scratch/<name>.md)
    terminal.py                 (NEW: PTY widget; gated on textual-terminal availability)
  app.py                        (MODIFY: register all 7 new widgets; pass widget metadata)
tests/
  test_orchestrator_tools_dict.py
  test_layout_engine_weakref.py
  test_config_general.py
  test_widget_registry_metadata.py
  test_orchestrator_tools_list_widgets.py
  test_widget_markdown.py
  test_widget_file_viewer.py
  test_widget_file_tree.py
  test_widget_diff_viewer.py
  test_widget_log_tail.py
  test_widget_notebook.py
  test_widget_terminal.py        (may be skipped if textual-terminal isn't viable)
  test_app_smoke_plan5.py        (e2e: orchestrator mounts a Markdown panel via set_layout)
```

---

## Task 1 — Carry-over: `build_orchestrator_tools` returns a dict

**Files:**
- Modify: `patchbai/orchestrator/tools.py`
- Modify: any test that does positional unpacking
- Test: `tests/test_orchestrator_tools_dict.py`

The plan-4 reviewer flagged that `build_orchestrator_tools` returns a tuple, so test files do `tools[7]`, `tools[11]`, `tools[12]` — brittle and tightly coupled to the order of `_SPECS` plus conditional appends. Returning a `dict[str, handler]` keyed by tool name is stable across additions.

- [ ] **Step 1: Write the failing test**

Create `tests/test_orchestrator_tools_dict.py`:

```python
import pytest

from patchbai.actions import ActionRegistry
from patchbai.agents.fake_sdk_adapter import FakeSDKAdapter
from patchbai.agents.manager import AgentManager
from patchbai.config import ConfigStore
from patchbai.events import EventBus
from patchbai.orchestrator.tools import build_orchestrator_tools
from patchbai.persistence.layouts_store import NamedLayoutsStore


def _make(tmp_path, ok_script):
    return AgentManager(
        cwd=tmp_path, bus=EventBus(),
        adapter_factory=lambda: FakeSDKAdapter(scripts=[ok_script()]),
    )


def test_build_orchestrator_tools_returns_dict_keyed_by_name(tmp_path, ok_script):
    manager = _make(tmp_path, ok_script)
    tools = build_orchestrator_tools(manager)
    assert isinstance(tools, dict)
    assert set(tools.keys()) == {
        "spawn_agent", "list_agents", "read_agent_transcript",
        "send_to_agent", "interrupt_agent", "kill_agent",
        "respond_to_agent_request",
    }


def test_build_orchestrator_tools_with_layout_kwargs(tmp_path, ok_script):
    manager = _make(tmp_path, ok_script)
    store = NamedLayoutsStore(global_dir=tmp_path)

    async def _apply(spec, *, layout_name=None):
        pass

    tools = build_orchestrator_tools(manager, apply_layout=_apply, layouts_store=store)
    assert "set_layout" in tools
    assert "save_layout" in tools
    assert "load_layout" in tools
    assert "list_layouts" in tools


def test_build_orchestrator_tools_with_config_kwargs(tmp_path, ok_script):
    manager = _make(tmp_path, ok_script)
    store = ConfigStore(global_dir=tmp_path)
    actions = ActionRegistry()

    tools = build_orchestrator_tools(manager, config_store=store, actions=actions)
    assert "bind_key" in tools
    assert "unbind_key" in tools
    assert "set_config" in tools
    assert "get_config" in tools
    assert "list_actions" in tools
    assert "list_bindings" in tools
```

- [ ] **Step 2: Run and confirm failure**

```bash
.venv/bin/pytest tests/test_orchestrator_tools_dict.py -v
```

Expected: failures (build_orchestrator_tools currently returns a tuple).

- [ ] **Step 3: Modify `patchbai/orchestrator/tools.py`**

Find `build_orchestrator_tools`. Replace its return statement and the loop that builds handlers with a name-keyed dict.

The base loop becomes:

```python
def build_orchestrator_tools(
    manager: AgentManager,
    *,
    apply_layout=None,
    layouts_store: NamedLayoutsStore | None = None,
    config_store: ConfigStore | None = None,
    actions: ActionRegistry | None = None,
    rebind_keys=None,
):
    """Return a dict {tool_name: async_handler} for unit testing."""
    handlers: dict = {}
    for spec in _SPECS:
        handlers[spec.name] = spec.build(manager)
    if apply_layout is not None and layouts_store is not None:
        handlers["set_layout"] = _set_layout_handler(apply_layout)
        handlers["save_layout"] = _save_layout_handler(layouts_store)
        handlers["load_layout"] = _load_layout_handler(apply_layout, layouts_store)
        handlers["list_layouts"] = _list_layouts_handler(layouts_store)
    if config_store is not None and actions is not None:
        handlers["bind_key"] = _bind_key_handler(config_store, actions, rebind_keys)
        handlers["unbind_key"] = _unbind_key_handler(config_store, rebind_keys)
        handlers["set_config"] = _set_config_handler(config_store)
        handlers["get_config"] = _get_config_handler(config_store)
        handlers["list_actions"] = _list_actions_handler(actions)
        handlers["list_bindings"] = _list_bindings_handler(config_store)
    return handlers
```

- [ ] **Step 4: Update existing tests that unpack positionally**

Existing tests use patterns like:

```python
spawn, _list, _read, _send, _interrupt, _kill, _respond = build_orchestrator_tools(manager)
```

Or:

```python
tools = build_orchestrator_tools(...)
set_layout = tools[7]
```

Find every call site (grep `build_orchestrator_tools` in `tests/`) and rewrite to dict access:

```python
tools = build_orchestrator_tools(manager)
spawn = tools["spawn_agent"]
```

```python
tools = build_orchestrator_tools(...)
set_layout = tools["set_layout"]
bind_key = tools["bind_key"]
# etc.
```

The files to update (verify with grep before editing):
- `tests/test_orchestrator_tools.py`
- `tests/test_orchestrator_tools_send_interrupt_kill.py`
- `tests/test_orchestrator_tools_respond.py`
- `tests/test_orchestrator_tools_layout.py`
- `tests/test_orchestrator_tools_config.py`
- `tests/test_app_smoke_plan2.py`
- `tests/test_app_smoke_plan3.py`
- `tests/test_app_smoke_plan4.py`

For each, change tuple-unpacking and indexed-access to dict-key access. The semantics stay identical.

- [ ] **Step 5: Run all tests**

```bash
.venv/bin/pytest -q
```

Expected: full suite green (164 + 1 skipped).

If any test still fails because it missed an unpack site, fix it. If a test fails because the dict ordering surprises it, that's a bug in the test (Python dicts are insertion-ordered, but tests should not depend on that).

- [ ] **Step 6: Commit**

```bash
git add patchbai/orchestrator/tools.py tests/test_orchestrator_tools_dict.py tests/test_orchestrator_tools.py tests/test_orchestrator_tools_send_interrupt_kill.py tests/test_orchestrator_tools_respond.py tests/test_orchestrator_tools_layout.py tests/test_orchestrator_tools_config.py tests/test_app_smoke_plan2.py tests/test_app_smoke_plan3.py tests/test_app_smoke_plan4.py
git commit -m "refactor(orchestrator): build_orchestrator_tools returns dict by name"
```

---

## Task 2 — Carry-over: `_last_applied_spec` uses `WeakKeyDictionary`

**Files:**
- Modify: `patchbai/layout/engine.py`
- Test: `tests/test_layout_engine_weakref.py`

`_last_applied_spec: dict[int, LayoutSpec]` uses `id(container)` as the key. CPython is allowed to reuse `id()` values after gc — a fresh container that happens to land at a previously-used `id` would incorrectly hit the fast-path. Switch to `WeakKeyDictionary` keyed by the container itself; entries gc out automatically when the container does.

- [ ] **Step 1: Write the failing test**

Create `tests/test_layout_engine_weakref.py`:

```python
import gc

import pytest
from textual.app import App
from textual.containers import Container

from patchbai.events import EventBus
from patchbai.layout import engine as engine_mod
from patchbai.layout.defaults import dashboard_layout
from patchbai.layout.engine import apply as apply_layout
from patchbai.layout.registry import WidgetRegistry
from patchbai.widgets.agent_table import AgentTable
from patchbai.widgets.orchestrator_chat import OrchestratorChat
from patchbai.widgets.placeholders import ActivityFeed


def _registry() -> WidgetRegistry:
    reg = WidgetRegistry()
    reg.register("OrchestratorChat", OrchestratorChat)
    reg.register("AgentTable", AgentTable)
    reg.register("ActivityFeed", ActivityFeed)
    return reg


class _HostApp(App):
    def __init__(self, bus: EventBus) -> None:
        super().__init__()
        self.event_bus = bus

    def compose(self):
        yield Container(id="panel-area")


@pytest.mark.asyncio
async def test_last_applied_spec_does_not_leak_after_container_gc():
    """After multiple App lifecycles, the cache must not grow unboundedly."""
    initial_size = len(engine_mod._last_applied_spec)

    for _ in range(5):
        bus = EventBus()
        app = _HostApp(bus)
        async with app.run_test() as pilot:
            await pilot.pause()
            area = app.query_one("#panel-area", Container)
            await apply_layout(area, dashboard_layout(), _registry())
            await pilot.pause()
        # App + Container go out of scope here.
        gc.collect()

    # WeakKeyDictionary entries should have been collected. Allow a small
    # tolerance in case some entries linger pending a future gc cycle.
    final_size = len(engine_mod._last_applied_spec)
    assert final_size <= initial_size + 1, (
        f"_last_applied_spec leaked: {initial_size=} → {final_size=}"
    )
```

- [ ] **Step 2: Run and observe**

```bash
.venv/bin/pytest tests/test_layout_engine_weakref.py -v
```

Expected: failure — `_last_applied_spec` is a regular dict keyed by `id(container)`, so entries pile up.

- [ ] **Step 3: Modify `patchbai/layout/engine.py`**

Replace the `_last_applied_spec` declaration and update its access sites:

```python
import weakref

# Track the most recent applied spec per container, keyed by the container
# instance itself. WeakKeyDictionary ensures stale entries gc out when the
# container is no longer referenced — important since `apply` is called many
# times per session in tests, and id-keyed caches are footguns once the
# garbage collector reuses ids.
_last_applied_spec: "weakref.WeakKeyDictionary[TxContainer, LayoutSpec]" = weakref.WeakKeyDictionary()
```

Replace the existing `_last_applied_spec.get(id(container))` and `_last_applied_spec[id(container)] = spec` lines in `apply` with:

```python
    if _last_applied_spec.get(container) == spec:
```

```python
    _last_applied_spec[container] = spec
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest tests/test_layout_engine_weakref.py tests/test_layout_engine_idempotent.py tests/test_layout_engine_focus.py -v
.venv/bin/pytest -q
```

Expected: new test passes; existing layout-engine tests still pass; full suite green.

- [ ] **Step 5: Commit**

```bash
git add patchbai/layout/engine.py tests/test_layout_engine_weakref.py
git commit -m "fix(layout): _last_applied_spec uses WeakKeyDictionary; no id() collision risk"
```

---

## Task 3 — Carry-over: generalize `Config.set_path` / `get_path` for any section

**Files:**
- Modify: `patchbai/config.py`
- Test: `tests/test_config_general.py`

Plan 4's `Config.set_path` / `get_path` only handle `ui.*`. Plan 5+ may need other sections (e.g., per-widget config). Generalize to walk the dataclass fields by section name.

- [ ] **Step 1: Write the failing test**

Create `tests/test_config_general.py`:

```python
import pytest

from patchbai.config import Config, ConfigStore


def test_get_path_works_for_unknown_section_after_extension(tmp_path, monkeypatch):
    """If we add a new section to Config, get_path/set_path should handle it
    without modifying their bodies. We monkeypatch a 'logs' section onto a
    Config instance to simulate a future addition."""
    cfg = Config()

    # Simulate a new dataclass-style section.
    class _LogsSection:
        level = "info"
        path = "/var/log/patchbai.log"

    cfg.logs = _LogsSection()  # type: ignore[attr-defined]

    assert cfg.get_path("logs.level") == "info"
    cfg.set_path("logs.level", "debug")
    assert cfg.get_path("logs.level") == "debug"


def test_get_path_unknown_section_raises():
    cfg = Config()
    with pytest.raises(KeyError):
        cfg.get_path("nonexistent.field")


def test_get_path_unknown_attr_raises():
    cfg = Config()
    with pytest.raises(KeyError):
        cfg.get_path("ui.nonexistent")


def test_get_path_invalid_format_raises():
    cfg = Config()
    with pytest.raises(KeyError):
        cfg.get_path("no_dots")
    with pytest.raises(KeyError):
        cfg.get_path("too.many.dots")
```

- [ ] **Step 2: Run and confirm**

```bash
.venv/bin/pytest tests/test_config_general.py -v
```

Expected: `test_get_path_works_for_unknown_section_after_extension` fails (current impl only knows `ui`); `test_get_path_unknown_attr_raises` may already pass via the existing branch.

- [ ] **Step 3: Modify `patchbai/config.py`**

Replace `Config.get_path` and `Config.set_path` with generic versions:

```python
    def get_path(self, path: str) -> Any:
        section, attr = self._split_path(path)
        section_obj = getattr(self, section, None)
        if section_obj is None or not hasattr(section_obj, attr):
            raise KeyError(path)
        return getattr(section_obj, attr)

    def set_path(self, path: str, value: Any) -> None:
        section, attr = self._split_path(path)
        section_obj = getattr(self, section, None)
        if section_obj is None or not hasattr(section_obj, attr):
            raise KeyError(path)
        setattr(section_obj, attr, value)

    @staticmethod
    def _split_path(path: str) -> tuple[str, str]:
        parts = path.split(".")
        if len(parts) != 2:
            raise KeyError(f"only dotted two-segment paths supported, got {path!r}")
        return parts[0], parts[1]
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest tests/test_config_general.py tests/test_config_store.py -v
.venv/bin/pytest -q
```

Expected: 4 new pass; existing config-store tests still pass; full suite green.

- [ ] **Step 5: Commit**

```bash
git add patchbai/config.py tests/test_config_general.py
git commit -m "refactor(config): generalize get_path/set_path to walk any dataclass section"
```

---

## Task 4 — `WidgetRegistry.register` accepts metadata; `list_widgets` MCP tool

**Files:**
- Modify: `patchbai/layout/registry.py`
- Modify: `patchbai/orchestrator/tools.py`
- Test: `tests/test_widget_registry_metadata.py`
- Test: `tests/test_orchestrator_tools_list_widgets.py`

For the orchestrator AI to use new widgets, it needs to know they exist + what props they take. Extend `WidgetRegistry.register` with optional `description` and `props_schema`. Add a `list_widgets` orchestrator tool that returns the metadata.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_widget_registry_metadata.py`:

```python
import pytest
from textual.widget import Widget

from patchbai.layout.registry import WidgetRegistry


class _W(Widget):
    pass


def test_register_with_description_and_schema_then_list():
    reg = WidgetRegistry()
    reg.register(
        "MyWidget", _W,
        description="does the thing",
        props_schema={"file_path": str},
    )
    info = reg.describe("MyWidget")
    assert info.name == "MyWidget"
    assert info.cls is _W
    assert info.description == "does the thing"
    assert info.props_schema == {"file_path": str}


def test_register_without_metadata_uses_defaults():
    reg = WidgetRegistry()
    reg.register("Plain", _W)
    info = reg.describe("Plain")
    assert info.description == ""
    assert info.props_schema == {}


def test_describe_unknown_raises():
    reg = WidgetRegistry()
    with pytest.raises(KeyError):
        reg.describe("Nope")


def test_describe_all_returns_sorted_metadata():
    reg = WidgetRegistry()
    reg.register("Beta", _W, description="b")
    reg.register("Alpha", _W, description="a")
    names = [m.name for m in reg.describe_all()]
    assert names == ["Alpha", "Beta"]
```

Create `tests/test_orchestrator_tools_list_widgets.py`:

```python
import json

import pytest
from textual.widget import Widget

from patchbai.agents.fake_sdk_adapter import FakeSDKAdapter
from patchbai.agents.manager import AgentManager
from patchbai.events import EventBus
from patchbai.layout.registry import WidgetRegistry
from patchbai.orchestrator.tools import build_orchestrator_tools


class _W(Widget):
    pass


@pytest.mark.asyncio
async def test_list_widgets_returns_registry_metadata(tmp_path, ok_script):
    reg = WidgetRegistry()
    reg.register("OrchestratorChat", _W, description="manager chat")
    reg.register("Markdown", _W, description="renders markdown",
                 props_schema={"source": str})

    manager = AgentManager(
        cwd=tmp_path, bus=EventBus(),
        adapter_factory=lambda: FakeSDKAdapter(scripts=[ok_script()]),
    )
    tools = build_orchestrator_tools(manager, widget_registry=reg)
    out = await tools["list_widgets"]({})
    parsed = json.loads(out["content"][0]["text"])
    by_name = {w["name"]: w for w in parsed}
    assert "OrchestratorChat" in by_name
    assert by_name["Markdown"]["description"] == "renders markdown"
    assert by_name["Markdown"]["props_schema"] == {"source": "str"}


@pytest.mark.asyncio
async def test_list_widgets_omitted_when_no_registry_passed(tmp_path, ok_script):
    manager = AgentManager(
        cwd=tmp_path, bus=EventBus(),
        adapter_factory=lambda: FakeSDKAdapter(scripts=[ok_script()]),
    )
    tools = build_orchestrator_tools(manager)  # no widget_registry kwarg
    assert "list_widgets" not in tools
```

- [ ] **Step 2: Run and confirm failures**

```bash
.venv/bin/pytest tests/test_widget_registry_metadata.py tests/test_orchestrator_tools_list_widgets.py -v
```

Expected: failures (no `describe`/`describe_all` on registry; no `list_widgets` tool; `widget_registry` kwarg unknown).

- [ ] **Step 3: Modify `patchbai/layout/registry.py`**

Replace the file's contents with:

```python
from dataclasses import dataclass, field

from textual.widget import Widget


class UnknownWidgetError(KeyError):
    """Raised when a LayoutSpec references a widget name that is not registered."""


@dataclass(frozen=True)
class WidgetInfo:
    name: str
    cls: type[Widget]
    description: str = ""
    props_schema: dict = field(default_factory=dict)


class WidgetRegistry:
    """Maps widget-type strings (as used in LayoutSpec) to Textual classes,
    plus optional metadata for the orchestrator's list_widgets tool.

    Mode-C `register_custom_widget(name, source)` (which `exec`s code into an
    isolated namespace) is intentionally NOT implemented in this plan — it
    arrives in plan 6.
    """

    def __init__(self) -> None:
        self._infos: dict[str, WidgetInfo] = {}

    def register(
        self,
        name: str,
        cls: type[Widget],
        *,
        description: str = "",
        props_schema: dict | None = None,
    ) -> None:
        self._infos[name] = WidgetInfo(
            name=name, cls=cls,
            description=description,
            props_schema=dict(props_schema) if props_schema else {},
        )

    def get(self, name: str) -> type[Widget]:
        if name not in self._infos:
            raise UnknownWidgetError(name)
        return self._infos[name].cls

    def known(self) -> list[str]:
        return list(self._infos.keys())

    def describe(self, name: str) -> WidgetInfo:
        if name not in self._infos:
            raise KeyError(name)
        return self._infos[name]

    def describe_all(self) -> list[WidgetInfo]:
        return sorted(self._infos.values(), key=lambda i: i.name)
```

- [ ] **Step 4: Modify `patchbai/orchestrator/tools.py`**

Add the import at the top:

```python
from patchbai.layout.registry import WidgetRegistry
```

Add a handler factory ABOVE `_SPECS` (near the other `_*_handler` functions):

```python
def _list_widgets_handler(registry: WidgetRegistry):
    async def list_widgets_tool(_args: dict) -> dict:
        out = []
        for info in registry.describe_all():
            out.append({
                "name": info.name,
                "description": info.description,
                "props_schema": {k: getattr(v, "__name__", str(v))
                                 for k, v in info.props_schema.items()},
            })
        return {"content": [{"type": "text", "text": json.dumps(out, indent=2)}]}
    return list_widgets_tool
```

Extend `build_orchestrator_tools` to accept `widget_registry`:

```python
def build_orchestrator_tools(
    manager: AgentManager,
    *,
    apply_layout=None,
    layouts_store: NamedLayoutsStore | None = None,
    config_store: ConfigStore | None = None,
    actions: ActionRegistry | None = None,
    rebind_keys=None,
    widget_registry: WidgetRegistry | None = None,
):
    handlers: dict = {}
    for spec in _SPECS:
        handlers[spec.name] = spec.build(manager)
    if apply_layout is not None and layouts_store is not None:
        handlers["set_layout"] = _set_layout_handler(apply_layout)
        handlers["save_layout"] = _save_layout_handler(layouts_store)
        handlers["load_layout"] = _load_layout_handler(apply_layout, layouts_store)
        handlers["list_layouts"] = _list_layouts_handler(layouts_store)
    if config_store is not None and actions is not None:
        handlers["bind_key"] = _bind_key_handler(config_store, actions, rebind_keys)
        handlers["unbind_key"] = _unbind_key_handler(config_store, rebind_keys)
        handlers["set_config"] = _set_config_handler(config_store)
        handlers["get_config"] = _get_config_handler(config_store)
        handlers["list_actions"] = _list_actions_handler(actions)
        handlers["list_bindings"] = _list_bindings_handler(config_store)
    if widget_registry is not None:
        handlers["list_widgets"] = _list_widgets_handler(widget_registry)
    return handlers
```

Mirror the addition in `build_orchestrator_mcp_server`:

```python
def build_orchestrator_mcp_server(
    manager: AgentManager,
    *,
    apply_layout=None,
    layouts_store: NamedLayoutsStore | None = None,
    config_store: ConfigStore | None = None,
    actions: ActionRegistry | None = None,
    rebind_keys=None,
    widget_registry: WidgetRegistry | None = None,
):
    sdk_tools = []
    # ...existing code that wraps base + layout + config tools...

    if widget_registry is not None:
        sdk_tools.append(tool(
            "list_widgets",
            "List all widgets registered in the layout registry, with their "
            "descriptions and prop schemas. Use this to discover what widgets "
            "you can include in a set_layout call.",
            {},
        )(_list_widgets_handler(widget_registry)))

    return create_sdk_mcp_server(
        name="patchbai_orchestrator", version="1.0.0", tools=sdk_tools,
    )
```

- [ ] **Step 5: Forward `widget_registry` from the OrchestratorSession**

In `patchbai/orchestrator/session.py`, extend `__init__` to accept `widget_registry=None`, store it, and forward in `start`:

In `__init__`, add the kwarg:

```python
        widget_registry=None,
```

After other `self._...` assignments:

```python
        self._widget_registry = widget_registry
```

In `start`, change the `build_orchestrator_mcp_server(...)` call to add:

```python
            widget_registry=self._widget_registry,
```

- [ ] **Step 6: Pass `widget_registry` from the App**

In `patchbai/app.py`, find the `OrchestratorSession(...)` construction in `__init__` and add `widget_registry=self.registry`:

```python
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
```

- [ ] **Step 7: Run tests**

```bash
.venv/bin/pytest tests/test_widget_registry_metadata.py tests/test_orchestrator_tools_list_widgets.py tests/test_registry.py -v
.venv/bin/pytest -q
```

Expected: new tests pass; existing registry test still passes; full suite green.

- [ ] **Step 8: Commit**

```bash
git add patchbai/layout/registry.py patchbai/orchestrator/tools.py patchbai/orchestrator/session.py patchbai/app.py tests/test_widget_registry_metadata.py tests/test_orchestrator_tools_list_widgets.py
git commit -m "feat(registry): widget metadata + list_widgets MCP tool"
```

---

## Task 5 — `Markdown` widget

**Files:**
- Create: `patchbai/widgets/markdown.py`
- Test: `tests/test_widget_markdown.py`

Thin wrapper around `textual.widgets.Markdown`. Accepts a `source: str` prop with markdown text, OR a `file_path: str` prop to load from disk. Subclasses Textual's `Markdown` so we can pass in source via constructor.

- [ ] **Step 1: Write the failing test**

Create `tests/test_widget_markdown.py`:

```python
from pathlib import Path

import pytest
from textual.app import App

from patchbai.widgets.markdown import Markdown


class _Host(App):
    def __init__(self, **kwargs):
        super().__init__()
        self._kwargs = kwargs

    def compose(self):
        yield Markdown(**self._kwargs)


@pytest.mark.asyncio
async def test_markdown_renders_inline_source():
    app = _Host(source="# Hello\n\nWorld")
    async with app.run_test() as pilot:
        await pilot.pause()
        widget = app.query_one(Markdown)
        # Just verify the widget mounted with our source content visible.
        assert "Hello" in widget._markdown


@pytest.mark.asyncio
async def test_markdown_loads_from_file_path(tmp_path: Path):
    md_path = tmp_path / "doc.md"
    md_path.write_text("# From file\n", encoding="utf-8")
    app = _Host(file_path=str(md_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        widget = app.query_one(Markdown)
        assert "From file" in widget._markdown


@pytest.mark.asyncio
async def test_markdown_missing_file_renders_error_text(tmp_path: Path):
    app = _Host(file_path=str(tmp_path / "nope.md"))
    async with app.run_test() as pilot:
        await pilot.pause()
        widget = app.query_one(Markdown)
        assert "not found" in widget._markdown.lower() or "missing" in widget._markdown.lower()
```

- [ ] **Step 2: Run and confirm failure**

```bash
.venv/bin/pytest tests/test_widget_markdown.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement `patchbai/widgets/markdown.py`**

```python
from pathlib import Path

from textual.widgets import Markdown as _TxMarkdown


class Markdown(_TxMarkdown):
    """Renders markdown text from `source` or `file_path`. The internal
    `_markdown` attribute holds the source string for tests."""

    def __init__(
        self,
        *,
        source: str | None = None,
        file_path: str | None = None,
    ) -> None:
        if source is None and file_path is not None:
            try:
                source = Path(file_path).read_text(encoding="utf-8")
            except FileNotFoundError:
                source = f"*File not found: {file_path}*"
            except Exception as e:
                source = f"*Error loading {file_path}: {e}*"
        if source is None:
            source = ""
        super().__init__(source)
        self._markdown = source
```

- [ ] **Step 4: Register in app.py**

In `build_default_registry`, add:

```python
    reg.register(
        "Markdown", Markdown,
        description="Renders markdown from `source` (string) or `file_path`.",
        props_schema={"source": str, "file_path": str},
    )
```

(Add the import at the top of `app.py`: `from patchbai.widgets.markdown import Markdown`.)

- [ ] **Step 5: Run tests**

```bash
.venv/bin/pytest tests/test_widget_markdown.py -v
.venv/bin/pytest -q
```

Expected: 3 new pass; full suite green.

- [ ] **Step 6: Commit**

```bash
git add patchbai/widgets/markdown.py patchbai/app.py tests/test_widget_markdown.py
git commit -m "feat(widgets): Markdown — wraps Textual's Markdown with source/file_path props"
```

---

## Task 6 — `FileViewer` widget

**Files:**
- Create: `patchbai/widgets/file_viewer.py`
- Test: `tests/test_widget_file_viewer.py`

Read-only file viewer using Textual's `TextArea` with `read_only=True` and `language` set from the file extension.

- [ ] **Step 1: Write the failing test**

Create `tests/test_widget_file_viewer.py`:

```python
from pathlib import Path

import pytest
from textual.app import App

from patchbai.widgets.file_viewer import FileViewer


class _Host(App):
    def __init__(self, file_path: str):
        super().__init__()
        self._file_path = file_path

    def compose(self):
        yield FileViewer(file_path=self._file_path)


@pytest.mark.asyncio
async def test_file_viewer_loads_text_content(tmp_path: Path):
    p = tmp_path / "hello.py"
    p.write_text("print('hi')\n", encoding="utf-8")

    app = _Host(str(p))
    async with app.run_test() as pilot:
        await pilot.pause()
        viewer = app.query_one(FileViewer)
        assert viewer.text.startswith("print('hi')")


@pytest.mark.asyncio
async def test_file_viewer_detects_python_language(tmp_path: Path):
    p = tmp_path / "x.py"
    p.write_text("x = 1\n", encoding="utf-8")

    app = _Host(str(p))
    async with app.run_test() as pilot:
        await pilot.pause()
        viewer = app.query_one(FileViewer)
        assert viewer.language == "python"


@pytest.mark.asyncio
async def test_file_viewer_missing_file_shows_error(tmp_path: Path):
    app = _Host(str(tmp_path / "nope.txt"))
    async with app.run_test() as pilot:
        await pilot.pause()
        viewer = app.query_one(FileViewer)
        assert "not found" in viewer.text.lower()
```

- [ ] **Step 2: Run and confirm failure**

```bash
.venv/bin/pytest tests/test_widget_file_viewer.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement `patchbai/widgets/file_viewer.py`**

```python
from pathlib import Path

from textual.widgets import TextArea


_EXTENSION_LANGUAGES = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "javascript",  # TextArea ships JS lexer; TS falls back well.
    ".tsx": "javascript",
    ".json": "json",
    ".html": "html",
    ".css": "css",
    ".md": "markdown",
    ".rs": "rust",
    ".go": "go",
    ".sh": "bash",
    ".bash": "bash",
    ".sql": "sql",
    ".toml": "toml",
    ".yaml": "yaml",
    ".yml": "yaml",
}


def _detect_language(path: Path) -> str | None:
    return _EXTENSION_LANGUAGES.get(path.suffix.lower())


class FileViewer(TextArea):
    """Read-only file display with extension-based syntax highlighting."""

    def __init__(self, *, file_path: str) -> None:
        path = Path(file_path)
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            text = f"File not found: {file_path}"
        except Exception as e:
            text = f"Error loading {file_path}: {e}"
        language = _detect_language(path)
        super().__init__(text, language=language, read_only=True)
```

- [ ] **Step 4: Register in app.py**

In `build_default_registry`, add:

```python
    reg.register(
        "FileViewer", FileViewer,
        description="Read-only syntax-highlighted file display.",
        props_schema={"file_path": str},
    )
```

Add import: `from patchbai.widgets.file_viewer import FileViewer`.

- [ ] **Step 5: Run tests**

```bash
.venv/bin/pytest tests/test_widget_file_viewer.py -v
.venv/bin/pytest -q
```

Expected: 3 new pass; full suite green.

If Textual's `TextArea` doesn't accept `language=None` (might require omitting the kwarg entirely), STOP and report. The fix is straightforward: pass `language` via kwargs only when set.

- [ ] **Step 6: Commit**

```bash
git add patchbai/widgets/file_viewer.py patchbai/app.py tests/test_widget_file_viewer.py
git commit -m "feat(widgets): FileViewer — read-only file display with syntax detection"
```

---

## Task 7 — `FileTree` widget

**Files:**
- Create: `patchbai/widgets/file_tree.py`
- Test: `tests/test_widget_file_tree.py`

Thin wrapper around `textual.widgets.DirectoryTree` with a `path: str` prop.

- [ ] **Step 1: Write the failing test**

Create `tests/test_widget_file_tree.py`:

```python
from pathlib import Path

import pytest
from textual.app import App

from patchbai.widgets.file_tree import FileTree


class _Host(App):
    def __init__(self, path: str):
        super().__init__()
        self._path = path

    def compose(self):
        yield FileTree(path=self._path)


@pytest.mark.asyncio
async def test_file_tree_mounts_with_path(tmp_path: Path):
    (tmp_path / "a.txt").write_text("a")
    (tmp_path / "subdir").mkdir()

    app = _Host(str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        tree = app.query_one(FileTree)
        assert str(tree.path) == str(tmp_path)
```

- [ ] **Step 2: Run and confirm failure**

```bash
.venv/bin/pytest tests/test_widget_file_tree.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement `patchbai/widgets/file_tree.py`**

```python
from pathlib import Path

from textual.widgets import DirectoryTree


class FileTree(DirectoryTree):
    """Wraps Textual's DirectoryTree with a kw-only `path` prop."""

    def __init__(self, *, path: str) -> None:
        super().__init__(Path(path))
```

- [ ] **Step 4: Register in app.py**

In `build_default_registry`, add:

```python
    reg.register(
        "FileTree", FileTree,
        description="Directory tree starting at `path`.",
        props_schema={"path": str},
    )
```

Add import: `from patchbai.widgets.file_tree import FileTree`.

- [ ] **Step 5: Run tests**

```bash
.venv/bin/pytest tests/test_widget_file_tree.py -v
.venv/bin/pytest -q
```

Expected: 1 new pass; full suite green.

- [ ] **Step 6: Commit**

```bash
git add patchbai/widgets/file_tree.py patchbai/app.py tests/test_widget_file_tree.py
git commit -m "feat(widgets): FileTree — DirectoryTree wrapper with path prop"
```

---

## Task 8 — `DiffViewer` widget

**Files:**
- Create: `patchbai/widgets/diff_viewer.py`
- Test: `tests/test_widget_diff_viewer.py`

Renders a unified diff. Accepts either a precomputed `diff: str` prop OR a `before: str` + `after: str` pair (we compute the diff with `difflib`). Display via Textual's `Static` with a `rich.syntax.Syntax` renderable for the `diff` lexer.

- [ ] **Step 1: Write the failing test**

Create `tests/test_widget_diff_viewer.py`:

```python
import pytest
from textual.app import App

from patchbai.widgets.diff_viewer import DiffViewer


class _Host(App):
    def __init__(self, **kwargs):
        super().__init__()
        self._kwargs = kwargs

    def compose(self):
        yield DiffViewer(**self._kwargs)


@pytest.mark.asyncio
async def test_diff_viewer_renders_precomputed_diff():
    diff = "--- a\n+++ b\n@@ -1 +1 @@\n-old\n+new\n"
    app = _Host(diff=diff)
    async with app.run_test() as pilot:
        await pilot.pause()
        viewer = app.query_one(DiffViewer)
        assert "+new" in viewer.diff_text
        assert "-old" in viewer.diff_text


@pytest.mark.asyncio
async def test_diff_viewer_computes_from_before_after():
    app = _Host(before="line 1\nline 2\n", after="line 1\nline 2 changed\n")
    async with app.run_test() as pilot:
        await pilot.pause()
        viewer = app.query_one(DiffViewer)
        assert "-line 2" in viewer.diff_text
        assert "+line 2 changed" in viewer.diff_text


@pytest.mark.asyncio
async def test_diff_viewer_no_inputs_renders_empty_message():
    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        viewer = app.query_one(DiffViewer)
        assert viewer.diff_text == "" or "no diff" in viewer.diff_text.lower()
```

- [ ] **Step 2: Run and confirm failure**

```bash
.venv/bin/pytest tests/test_widget_diff_viewer.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement `patchbai/widgets/diff_viewer.py`**

```python
import difflib

from rich.syntax import Syntax
from textual.containers import VerticalScroll
from textual.widgets import Static


def _compute_diff(before: str, after: str) -> str:
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile="before",
            tofile="after",
        )
    )


class DiffViewer(VerticalScroll):
    """Scrollable unified-diff viewer.

    Accepts either a precomputed `diff: str` or a `before` + `after` pair from
    which a unified diff is computed. The result is rendered as syntax-
    highlighted `diff` content.
    """

    DEFAULT_CSS = """
    DiffViewer {
        border: round $surface-lighten-2;
        padding: 0 1;
    }
    """

    def __init__(
        self,
        *,
        diff: str | None = None,
        before: str | None = None,
        after: str | None = None,
    ) -> None:
        super().__init__()
        if diff is None and (before is not None or after is not None):
            diff = _compute_diff(before or "", after or "")
        self.diff_text = diff or ""

    def compose(self):
        if self.diff_text:
            yield Static(Syntax(self.diff_text, "diff", theme="ansi_dark"))
        else:
            yield Static("[dim]No diff to display[/dim]")
```

- [ ] **Step 4: Register in app.py**

In `build_default_registry`:

```python
    reg.register(
        "DiffViewer", DiffViewer,
        description=(
            "Unified-diff viewer. Pass a precomputed `diff` string, OR pass "
            "`before` + `after` strings for unified-diff computation."
        ),
        props_schema={"diff": str, "before": str, "after": str},
    )
```

Add import: `from patchbai.widgets.diff_viewer import DiffViewer`.

- [ ] **Step 5: Run tests**

```bash
.venv/bin/pytest tests/test_widget_diff_viewer.py -v
.venv/bin/pytest -q
```

Expected: 3 new pass; full suite green.

- [ ] **Step 6: Commit**

```bash
git add patchbai/widgets/diff_viewer.py patchbai/app.py tests/test_widget_diff_viewer.py
git commit -m "feat(widgets): DiffViewer — unified diff with rich syntax highlighting"
```

---

## Task 9 — `LogTail` widget

**Files:**
- Create: `patchbai/widgets/log_tail.py`
- Test: `tests/test_widget_log_tail.py`

Tails a file: opens it, reads existing content (last `tail_lines` lines), then polls every 250ms for new content and appends. Uses Textual's `set_interval` for the timer.

- [ ] **Step 1: Write the failing test**

Create `tests/test_widget_log_tail.py`:

```python
from pathlib import Path

import pytest
from textual.app import App

from patchbai.widgets.log_tail import LogTail


class _Host(App):
    def __init__(self, file_path: str):
        super().__init__()
        self._file_path = file_path

    def compose(self):
        yield LogTail(file_path=self._file_path)


@pytest.mark.asyncio
async def test_log_tail_renders_existing_content(tmp_path: Path):
    p = tmp_path / "x.log"
    p.write_text("first line\nsecond line\n")

    app = _Host(str(p))
    async with app.run_test() as pilot:
        await pilot.pause()
        tail = app.query_one(LogTail)
        assert "first line" in tail.text
        assert "second line" in tail.text


@pytest.mark.asyncio
async def test_log_tail_appends_new_lines(tmp_path: Path):
    p = tmp_path / "x.log"
    p.write_text("initial\n")

    app = _Host(str(p))
    async with app.run_test() as pilot:
        await pilot.pause()
        tail = app.query_one(LogTail)
        assert "initial" in tail.text

        # Append while the widget is mounted.
        with p.open("a") as f:
            f.write("appended\n")

        # Trigger the tick manually (avoids waiting for the 250ms timer).
        tail._tick()
        await pilot.pause()
        assert "appended" in tail.text


@pytest.mark.asyncio
async def test_log_tail_missing_file_shows_error(tmp_path: Path):
    app = _Host(str(tmp_path / "nope.log"))
    async with app.run_test() as pilot:
        await pilot.pause()
        tail = app.query_one(LogTail)
        assert "not found" in tail.text.lower()
```

- [ ] **Step 2: Run and confirm failure**

```bash
.venv/bin/pytest tests/test_widget_log_tail.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement `patchbai/widgets/log_tail.py`**

```python
from pathlib import Path

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Static


class LogTail(VerticalScroll):
    """Tails a file: shows existing content, polls every 250ms for additions."""

    DEFAULT_CSS = """
    LogTail {
        border: round $surface-lighten-2;
        padding: 0 1;
    }
    """

    def __init__(self, *, file_path: str, tail_lines: int = 200) -> None:
        super().__init__()
        self._path = Path(file_path)
        self._tail_lines = tail_lines
        self._fp = None
        self.text = ""
        self._timer = None

    def compose(self) -> ComposeResult:
        yield Static("", id="log-tail-content")

    def on_mount(self) -> None:
        if not self._path.exists():
            self.text = f"File not found: {self._path}"
            self._update_static()
            return
        # Read last N lines of existing content.
        try:
            lines = self._path.read_text(encoding="utf-8", errors="replace").splitlines()
            self.text = "\n".join(lines[-self._tail_lines:])
        except Exception as e:
            self.text = f"Error reading {self._path}: {e}"
            self._update_static()
            return
        self._update_static()
        # Open for incremental reads from the end.
        try:
            self._fp = self._path.open("r", encoding="utf-8", errors="replace")
            self._fp.seek(0, 2)  # end
        except Exception:
            self._fp = None
        self._timer = self.set_interval(0.25, self._tick)

    def on_unmount(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        if self._fp is not None:
            try:
                self._fp.close()
            except Exception:
                pass
            self._fp = None

    def _tick(self) -> None:
        if self._fp is None:
            return
        new = self._fp.read()
        if not new:
            return
        self.text = (self.text + "\n" + new).strip("\n")
        self._update_static()
        self.scroll_end(animate=False)

    def _update_static(self) -> None:
        try:
            self.query_one("#log-tail-content", Static).update(self.text)
        except Exception:
            pass
```

- [ ] **Step 4: Register in app.py**

In `build_default_registry`:

```python
    reg.register(
        "LogTail", LogTail,
        description=(
            "Tails an arbitrary file. Polls every 250ms. Optional "
            "`tail_lines` controls how much of the existing tail is shown."
        ),
        props_schema={"file_path": str, "tail_lines": int},
    )
```

Add import.

- [ ] **Step 5: Run tests**

```bash
.venv/bin/pytest tests/test_widget_log_tail.py -v
.venv/bin/pytest -q
```

Expected: 3 new pass; full suite green.

- [ ] **Step 6: Commit**

```bash
git add patchbai/widgets/log_tail.py patchbai/app.py tests/test_widget_log_tail.py
git commit -m "feat(widgets): LogTail — file tailer with 250ms poll"
```

---

## Task 10 — `Notebook` widget

**Files:**
- Create: `patchbai/widgets/notebook.py`
- Test: `tests/test_widget_notebook.py`

Persistent scratch space: editable `TextArea` whose contents are loaded from `<cwd>/.patchbai/scratch/<name>.md` on mount and saved back on every change.

- [ ] **Step 1: Write the failing test**

Create `tests/test_widget_notebook.py`:

```python
from pathlib import Path

import pytest
from textual.app import App

from patchbai.widgets.notebook import Notebook


class _Host(App):
    def __init__(self, name: str, cwd: Path):
        super().__init__()
        self.cwd = cwd
        self._name = name

    def compose(self):
        yield Notebook(name=self._name)


@pytest.mark.asyncio
async def test_notebook_loads_existing_content(tmp_path: Path):
    scratch = tmp_path / ".patchbai" / "scratch"
    scratch.mkdir(parents=True)
    (scratch / "todo.md").write_text("- one\n- two\n", encoding="utf-8")

    app = _Host("todo", tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        nb = app.query_one(Notebook)
        assert "one" in nb.text
        assert "two" in nb.text


@pytest.mark.asyncio
async def test_notebook_persists_edits(tmp_path: Path):
    app = _Host("todo", tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        nb = app.query_one(Notebook)
        nb.text = "- new entry\n"
        # Manually trigger save (Notebook saves on text-changed; we drive it).
        nb._save()
        await pilot.pause()

    saved = (tmp_path / ".patchbai" / "scratch" / "todo.md").read_text(encoding="utf-8")
    assert "new entry" in saved


@pytest.mark.asyncio
async def test_notebook_creates_scratch_dir(tmp_path: Path):
    app = _Host("fresh", tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        nb = app.query_one(Notebook)
        nb.text = "x\n"
        nb._save()
        await pilot.pause()
    assert (tmp_path / ".patchbai" / "scratch" / "fresh.md").exists()
```

- [ ] **Step 2: Run and confirm failure**

```bash
.venv/bin/pytest tests/test_widget_notebook.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement `patchbai/widgets/notebook.py`**

```python
from pathlib import Path

from textual.widgets import TextArea


class Notebook(TextArea):
    """Persistent scratch buffer at <cwd>/.patchbai/scratch/<name>.md."""

    def __init__(self, *, name: str) -> None:
        super().__init__("", language="markdown")
        self._name = name

    def _path(self) -> Path:
        cwd = getattr(self.app, "cwd", Path.cwd())
        return Path(cwd) / ".patchbai" / "scratch" / f"{self._name}.md"

    def on_mount(self) -> None:
        path = self._path()
        if path.exists():
            try:
                self.text = path.read_text(encoding="utf-8")
            except Exception:
                pass

    def _save(self) -> None:
        path = self._path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.text, encoding="utf-8")

    def on_text_area_changed(self, _event) -> None:
        # Saves on every keystroke. Cheap for a scratchpad-sized file.
        self._save()
```

- [ ] **Step 4: Register in app.py**

In `build_default_registry`:

```python
    reg.register(
        "Notebook", Notebook,
        description=(
            "Editable scratch buffer; persists to <cwd>/.patchbai/scratch/<name>.md."
        ),
        props_schema={"name": str},
    )
```

Add import.

- [ ] **Step 5: Run tests**

```bash
.venv/bin/pytest tests/test_widget_notebook.py -v
.venv/bin/pytest -q
```

Expected: 3 new pass; full suite green.

- [ ] **Step 6: Commit**

```bash
git add patchbai/widgets/notebook.py patchbai/app.py tests/test_widget_notebook.py
git commit -m "feat(widgets): Notebook — persistent scratch buffer in .patchbai/scratch/"
```

---

## Task 11 — Add `textual-terminal` dependency (gated)

**Files:**
- Modify: `pyproject.toml`

The `Terminal` widget depends on a third-party PTY package. We try `textual-terminal` first.

- [ ] **Step 1: Attempt install**

```bash
cd /Users/jimmy.mills/Developer/patchbai
uv pip install textual-terminal
```

Expected: clean install. If pip reports an error (missing wheel for current Textual version, abandoned package), STOP and report — Task 12 will be marked BLOCKED and `Terminal` widget skipped from this plan.

- [ ] **Step 2: Verify import surface**

```bash
.venv/bin/python -c "
import textual_terminal as tt
print('exports:', sorted(n for n in dir(tt) if not n.startswith('_')))
"
```

Expected: prints a list including `Terminal` (or similar). Note the actual class name — Task 12 will use it.

If the class isn't called `Terminal` (e.g., it's `TextualTerminal`, `PtyTerminal`), note the actual name and pass it forward to Task 12.

- [ ] **Step 3: Add the dep to pyproject.toml**

In the `[project] dependencies` block:

```toml
dependencies = [
  "textual>=0.80",
  "pydantic>=2.6",
  "claude-agent-sdk>=0.1",
  "tomli-w>=1.0",
  "textual-terminal>=0.3",
]
```

(Use whatever version `pip install` reported as installed; bump the floor to that.)

- [ ] **Step 4: Sync + suite green**

```bash
uv pip install -e ".[dev]"
.venv/bin/pytest -q
```

Expected: full suite green; `textual-terminal` is now installed alongside.

- [ ] **Step 5: Commit OR Skip**

If Step 1 succeeded:

```bash
git add pyproject.toml
git commit -m "chore: add textual-terminal dependency for the Terminal widget"
```

If Step 1 BLOCKED (no working PTY widget), do NOT modify pyproject.toml. Report status BLOCKED on this task; Task 12 then ships a stub or is also skipped.

---

## Task 12 — `Terminal` widget (PTY)

**Files:**
- Create: `patchbai/widgets/terminal.py`
- Test: `tests/test_widget_terminal.py`

A Textual widget hosting a real PTY. Wraps the third-party widget added in Task 11. Accepts `command: list[str]` (defaults to user's `$SHELL`), `cwd: str`, `env: dict`.

If Task 11 was BLOCKED, this task is also BLOCKED — skip it and proceed to Task 13.

- [ ] **Step 1: Write the failing test**

Create `tests/test_widget_terminal.py`:

```python
import os

import pytest

# Skip the entire module if textual-terminal isn't installed.
pytest.importorskip("textual_terminal")

from textual.app import App

from patchbai.widgets.terminal import Terminal


class _Host(App):
    def __init__(self, **kwargs):
        super().__init__()
        self._kwargs = kwargs

    def compose(self):
        yield Terminal(**self._kwargs)


@pytest.mark.asyncio
async def test_terminal_mounts_with_default_shell():
    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        term = app.query_one(Terminal)
        # Just verify it mounted; we can't reliably assert PTY behavior in tests.
        assert term is not None


@pytest.mark.asyncio
async def test_terminal_mounts_with_custom_command(tmp_path):
    app = _Host(command=["/bin/echo", "hello"], cwd=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        term = app.query_one(Terminal)
        assert term is not None
```

- [ ] **Step 2: Run and confirm failure**

```bash
.venv/bin/pytest tests/test_widget_terminal.py -v
```

Expected: ImportError on `patchbai.widgets.terminal` (or skip if textual-terminal not installed).

- [ ] **Step 3: Implement `patchbai/widgets/terminal.py`**

```python
import os

# textual-terminal exposes its widget at the package root; the actual class
# name was discovered in Task 11. Adjust the import below if that task found
# a different name.
from textual_terminal import Terminal as _PtyTerminal


def _default_command() -> list[str]:
    shell = os.environ.get("SHELL", "/bin/sh")
    return [shell]


class Terminal(_PtyTerminal):
    """Real PTY hosted in a Textual panel.

    Props:
      command: list of argv to exec (default: user's $SHELL).
      cwd: working directory (default: process cwd).
      env: extra environment variables to merge into os.environ.

    NB: anything the user types here is opaque to the orchestrator. Use this
    when you want a real `claude` CLI session inside patchbai rather than the
    SDK-managed transcript view.
    """

    def __init__(
        self,
        *,
        command: list[str] | None = None,
        cwd: str | None = None,
        env: dict | None = None,
    ) -> None:
        argv = command or _default_command()
        environ = dict(os.environ)
        if env:
            environ.update(env)
        # The textual-terminal API surface shifts across versions. Pass what
        # we can; if the constructor doesn't accept some kwargs, the package
        # will surface a clear TypeError and Task 12 will need to adjust.
        super().__init__(command=argv, cwd=cwd, env=environ)
```

If the `_PtyTerminal.__init__` signature differs from `(command, cwd, env)`, STOP and report. The fix is to adapt the kwargs to whatever the package expects.

- [ ] **Step 4: Register in app.py (only if Task 11 succeeded)**

In `build_default_registry`:

```python
    reg.register(
        "Terminal", Terminal,
        description=(
            "Real PTY in a panel. Use this for an interactive `claude` CLI "
            "session inside patchbai — anything typed here is OPAQUE to the "
            "orchestrator (this is intentional escape-hatch behavior)."
        ),
        props_schema={"command": list, "cwd": str, "env": dict},
    )
```

Add import: `from patchbai.widgets.terminal import Terminal`. Wrap in a `try/except ImportError` if you want graceful degradation when `textual-terminal` is missing — but if Task 11 succeeded, the import should always work.

- [ ] **Step 5: Run tests**

```bash
.venv/bin/pytest tests/test_widget_terminal.py -v
.venv/bin/pytest -q
```

Expected: 2 new pass (or skipped if textual-terminal isn't installed); full suite green.

- [ ] **Step 6: Commit**

```bash
git add patchbai/widgets/terminal.py patchbai/app.py tests/test_widget_terminal.py
git commit -m "feat(widgets): Terminal — real PTY panel via textual-terminal"
```

---

## Task 13 — End-to-end smoke: orchestrator mounts a Markdown panel

**Files:**
- Create: `tests/test_app_smoke_plan5.py`

Drives a `set_layout` that includes the new `Markdown` widget and asserts it mounted with the right text content.

- [ ] **Step 1: Write the test**

Create `tests/test_app_smoke_plan5.py`:

```python
import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

from patchbai.agents.fake_sdk_adapter import FakeSDKAdapter
from patchbai.agents.manager import AgentManager
from patchbai.app import PatchbaiApp
from patchbai.events import EventBus
from patchbai.orchestrator.session import OrchestratorSession
from patchbai.orchestrator.tools import build_orchestrator_tools
from patchbai.widgets.markdown import Markdown


def _ok():
    return [
        AssistantMessage(content=[TextBlock(text="ok")], model="fake-model"),
        ResultMessage(
            subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
            num_turns=1, session_id="fake", total_cost_usd=0.0,
            usage={"input_tokens": 1, "output_tokens": 1}, result="ok",
        ),
    ]


@pytest.mark.asyncio
async def test_orchestrator_can_set_layout_with_markdown_panel(tmp_path):
    bus = EventBus()
    manager = AgentManager(
        cwd=tmp_path, bus=bus,
        adapter_factory=lambda: FakeSDKAdapter(scripts=[_ok()]),
    )
    app = PatchbaiApp(cwd=tmp_path, manager=manager, global_dir=tmp_path)
    app.event_bus = bus
    app.orchestrator = OrchestratorSession(
        cwd=tmp_path, bus=bus, manager=manager,
        adapter=FakeSDKAdapter(scripts=[_ok()]),
        apply_layout=app._orchestrator_apply_layout,
        layouts_store=app.layouts_store,
        config_store=app.config_store,
        actions=app.actions_registry,
        rebind_keys=app._rebind_keys,
        widget_registry=app.registry,
    )

    async with app.run_test() as pilot:
        await pilot.pause()

        tools = build_orchestrator_tools(
            app.manager,
            apply_layout=app._orchestrator_apply_layout,
            layouts_store=app.layouts_store,
            config_store=app.config_store,
            actions=app.actions_registry,
            rebind_keys=app._rebind_keys,
            widget_registry=app.registry,
        )

        spec = {
            "version": 1,
            "layout": {
                "type": "horizontal",
                "children": [
                    {"id": "orch", "size": "60%", "widget": "OrchestratorChat"},
                    {
                        "id": "doc", "size": "40%",
                        "widget": "Markdown",
                        "props": {"source": "# Plan 5\n\nMarkdown panel works."},
                    },
                ],
            },
            "focus": "orch",
        }
        out = await tools["set_layout"]({"spec": spec})
        assert "applied" in out["content"][0]["text"].lower()
        await pilot.pause()

        md = app.query_one(Markdown)
        assert "Plan 5" in md._markdown
```

- [ ] **Step 2: Run**

```bash
.venv/bin/pytest tests/test_app_smoke_plan5.py -v
.venv/bin/pytest -q
```

Expected: 1 new pass; full suite green.

- [ ] **Step 3: Commit**

```bash
git add tests/test_app_smoke_plan5.py
git commit -m "test(app): plan-5 e2e — orchestrator mounts a Markdown panel via set_layout"
```

---

## Task 14 — Manual launch verification + tag `plan-5-complete`

- [ ] **Step 1: Imports**

```bash
cd /Users/jimmy.mills/Developer/patchbai && .venv/bin/python -c "
from patchbai.app import PatchbaiApp
from patchbai.widgets.markdown import Markdown
from patchbai.widgets.file_viewer import FileViewer
from patchbai.widgets.file_tree import FileTree
from patchbai.widgets.diff_viewer import DiffViewer
from patchbai.widgets.log_tail import LogTail
from patchbai.widgets.notebook import Notebook
print('plan 5 imports OK')

# Terminal is gated on textual-terminal availability.
try:
    from patchbai.widgets.terminal import Terminal
    print('Terminal: available')
except ImportError as e:
    print('Terminal: NOT available —', e)
"
```

Expected: `plan 5 imports OK` plus a Terminal status line. If Terminal is available, that's the desired state. If not, the rest still ships.

- [ ] **Step 2: Full suite**

```bash
.venv/bin/pytest -v
```

Expected: every non-skipped test passes; the real-SDK smoke + (possibly) the Terminal smoke skip cleanly.

- [ ] **Step 3: Commit any leftover docs**

```bash
git status
```

Plan doc was committed earlier. Note any untracked files.

- [ ] **Step 4: Tag**

```bash
git tag plan-5-complete
git tag --list
```

Expected: tag list includes `walking-skeleton-complete`, `plan-2-complete`, `plan-3-complete`, `plan-4-complete`, `plan-5-complete`.

---

## Self-review notes (for the writer of this plan, already verified)

- **Spec coverage:** plan-5 brainstorming targets — DiffViewer (Task 8), FileTree (Task 7), FileViewer (Task 6), LogTail (Task 9), Markdown (Task 5), Notebook (Task 10), Terminal (Tasks 11+12). All covered.
- **Carry-overs:** orchestrator-tools-as-dict (Task 1), WeakKeyDictionary for _last_applied_spec (Task 2), generalized Config.set_path (Task 3). All done at the front.
- **Bonus tool:** `list_widgets` (Task 4) so the orchestrator AI can discover what it can mount.
- **Placeholder scan:** no "TODO" / "TBD" / "implement later". Every step has actual code or commands. The Terminal widget has explicit STOP-and-report instructions if `textual-terminal` doesn't install.
- **Type consistency:** `Markdown`, `FileViewer`, `FileTree`, `DiffViewer`, `LogTail`, `Notebook`, `Terminal`, `WidgetInfo`, `WidgetRegistry.describe` / `.describe_all`, `_list_widgets_handler`, `widget_registry` kwarg — names used identically across all tasks.
- **Risk areas:**
  - Task 11/12: `textual-terminal` may not install or its API may differ. Documented STOP-and-report. The plan ships even if Terminal is dropped.
  - Task 6: `TextArea(text, language=None, read_only=True)` — Textual's TextArea API may not accept `language=None`. Fallback documented.
  - Task 9: `LogTail._tick` is invoked manually in the test (not via the 250ms timer) — verify this works on Textual 8.2.5; if `set_interval` returns a different timer object, adjust.
- **Tool count after plan 5:** orchestrator now has up to 18 MCP tools (7 base + 4 layout + 6 config + 1 list_widgets). Children unchanged at 2.
