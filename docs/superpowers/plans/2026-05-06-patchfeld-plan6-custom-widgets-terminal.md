# Patchfeld Plan 6 — Mode-C Custom Widgets + Terminal

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the spec. Mode-C: orchestrator can ship a `custom_widgets: [{name, source}]` block in `set_layout`, the source is `exec`'d in-process into an isolated namespace, the resulting Textual `Widget` subclass is registered into the live `WidgetRegistry`, and panels can reference it by name. Terminal: a real PTY hosted in a Textual panel via `ptyprocess` + `pyte` (since `textual-terminal` is dead). Plus three plan-5 carry-overs: `LogTail` survives file rotation, `FileViewer` documents its memory caveat, the misleading `DiffViewer` test gets cleaned up.

**Architecture:** A new `patchfeld/layout/custom_widgets.py` exposes `register_custom_widget(registry, name, source)` — `exec`s `source` in a namespace seeded with `textual.widget.Widget` and other common Textual imports, finds the resulting `Widget` subclass (preferring one named `WIDGET_CLASS` or matching `name`), and registers it in the supplied `WidgetRegistry`. The orchestrator's `_set_layout_handler` walks `spec.custom_widgets` before calling `apply_layout` — atomic: if any registration raises, the layout apply is aborted and `LayoutFailed` fires. Terminal uses `ptyprocess.PtyProcessUnicode` to fork a child shell, feeds incoming bytes through `pyte.ByteStream`+`pyte.Screen` for ANSI emulation, and renders the screen state to a `Static` on a 50ms tick. Keystrokes route from Textual's key events back into the PTY.

**Tech Stack:** Python 3.11+, Textual, pydantic v2, `claude-agent-sdk`, `tomli-w`, **NEW:** `ptyprocess` (PTY subprocess), `pyte` (terminal emulator), pytest + pytest-asyncio.

**Non-goals (final plan, but parking lot for v2):**
- Subprocess-sandboxed custom widgets (mode-C in plan 6 is in-process trust + try/except)
- Replacing `bypassPermissions` with a Textual approval modal (separate concern)
- Peer-to-peer messaging between child agents (out of scope for v1 entirely)
- Full session resume (`resume=session_id`) — the existing transcript persistence is sufficient
- Windows support for Terminal (ptyprocess is POSIX-only; Windows users get an error message)

---

## File Structure

```
patchfeld/
  layout/
    custom_widgets.py        (NEW: register_custom_widget — exec sandbox + class detection)
    registry.py              (MODIFY: add unregister(name) for hot-reload of mode-C re-emits)
  orchestrator/
    tools.py                 (MODIFY: _set_layout_handler walks spec.custom_widgets first)
  widgets/
    terminal.py              (NEW: ptyprocess + pyte PTY widget)
    log_tail.py              (MODIFY: detect file rotation; close+reopen on inode change)
    file_viewer.py           (MODIFY: docstring note about memory)
  app.py                     (MODIFY: register Terminal in build_default_registry)
tests/
  test_custom_widgets_register.py
  test_layout_registry_unregister.py
  test_orchestrator_tools_custom_widgets.py
  test_widget_terminal.py
  test_widget_log_tail_rotation.py
  test_widget_diff_viewer.py     (MODIFY: tighten misleading assertion)
  test_app_smoke_plan6.py        (e2e: orchestrator declares + uses a custom widget)
```

---

## Task 1 — `WidgetRegistry.unregister`

**Files:**
- Modify: `patchfeld/layout/registry.py`
- Test: `tests/test_layout_registry_unregister.py`

Mode-C custom widgets are re-registered every `set_layout` call. We need a way to drop a previous registration so a new source replaces the old class cleanly.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_layout_registry_unregister.py`:

```python
import pytest
from textual.widget import Widget

from patchfeld.layout.registry import UnknownWidgetError, WidgetRegistry


class _W(Widget):
    pass


def test_unregister_removes_known_widget():
    reg = WidgetRegistry()
    reg.register("MyWidget", _W)
    reg.unregister("MyWidget")
    with pytest.raises(UnknownWidgetError):
        reg.get("MyWidget")


def test_unregister_unknown_is_noop():
    reg = WidgetRegistry()
    reg.unregister("NeverRegistered")  # must not raise


def test_register_after_unregister_replaces_cleanly():
    reg = WidgetRegistry()
    reg.register("X", _W, description="v1")
    reg.unregister("X")

    class _V2(Widget):
        pass

    reg.register("X", _V2, description="v2")
    assert reg.get("X") is _V2
    assert reg.describe("X").description == "v2"
```

- [ ] **Step 2: Run and confirm failure**

```bash
.venv/bin/pytest tests/test_layout_registry_unregister.py -v
```

Expected: AttributeError on `unregister`.

- [ ] **Step 3: Add `unregister` to `patchfeld/layout/registry.py`**

Add this method to the `WidgetRegistry` class (anywhere after `register`):

```python
    def unregister(self, name: str) -> None:
        """Remove a widget registration. No-op if `name` was never registered."""
        self._infos.pop(name, None)
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest tests/test_layout_registry_unregister.py -v
.venv/bin/pytest -q
```

Expected: 3 new pass; full suite green (198 + 1 skipped).

- [ ] **Step 5: Commit**

```bash
git add patchfeld/layout/registry.py tests/test_layout_registry_unregister.py
git commit -m "feat(registry): WidgetRegistry.unregister for mode-C hot-reload"
```

---

## Task 2 — `register_custom_widget` exec sandbox

**Files:**
- Create: `patchfeld/layout/custom_widgets.py`
- Test: `tests/test_custom_widgets_register.py`

Takes (registry, name, source), `exec`s `source` in a namespace seeded with `textual.widget.Widget` and helpful Textual imports, finds the resulting `Widget` subclass, and registers it. Class-detection precedence:

1. A name `WIDGET_CLASS` in the namespace → use that.
2. A class named exactly `name` in the namespace → use that.
3. A single `Widget` subclass defined in the namespace → use that.
4. Otherwise raise `CustomWidgetError`.

If `exec` itself raises, wrap and re-raise as `CustomWidgetError`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_custom_widgets_register.py`:

```python
import pytest
from textual.widget import Widget

from patchfeld.layout.custom_widgets import (
    CustomWidgetError,
    register_custom_widget,
)
from patchfeld.layout.registry import WidgetRegistry


def test_register_with_widget_class_sentinel():
    reg = WidgetRegistry()
    src = """
from textual.widgets import Static

class MyPanel(Static):
    pass

WIDGET_CLASS = MyPanel
"""
    register_custom_widget(reg, "Banner", src)
    cls = reg.get("Banner")
    assert issubclass(cls, Widget)
    assert cls.__name__ == "MyPanel"


def test_register_class_named_after_widget_name():
    reg = WidgetRegistry()
    src = """
from textual.widgets import Static

class Banner(Static):
    pass
"""
    register_custom_widget(reg, "Banner", src)
    cls = reg.get("Banner")
    assert cls.__name__ == "Banner"


def test_register_single_widget_subclass_inferred():
    reg = WidgetRegistry()
    src = """
from textual.widgets import Static

class TheOnlyOne(Static):
    pass
"""
    register_custom_widget(reg, "Anything", src)
    cls = reg.get("Anything")
    assert cls.__name__ == "TheOnlyOne"


def test_register_raises_when_no_widget_subclass():
    reg = WidgetRegistry()
    src = "x = 42\n"
    with pytest.raises(CustomWidgetError, match="no Widget subclass"):
        register_custom_widget(reg, "Nope", src)


def test_register_raises_on_exec_error():
    reg = WidgetRegistry()
    src = "this is not valid python\n"
    with pytest.raises(CustomWidgetError, match="exec"):
        register_custom_widget(reg, "Nope", src)


def test_register_raises_when_multiple_widget_subclasses_and_no_sentinel():
    reg = WidgetRegistry()
    src = """
from textual.widgets import Static

class A(Static):
    pass

class B(Static):
    pass
"""
    with pytest.raises(CustomWidgetError, match="ambiguous"):
        register_custom_widget(reg, "X", src)


def test_register_re_register_replaces_class():
    reg = WidgetRegistry()
    src_v1 = """
from textual.widgets import Static
class MyPanel(Static):
    pass
WIDGET_CLASS = MyPanel
"""
    src_v2 = """
from textual.widgets import Static
class MyPanelV2(Static):
    pass
WIDGET_CLASS = MyPanelV2
"""
    register_custom_widget(reg, "Panel", src_v1)
    register_custom_widget(reg, "Panel", src_v2)
    cls = reg.get("Panel")
    assert cls.__name__ == "MyPanelV2"
```

- [ ] **Step 2: Run and confirm failure**

```bash
.venv/bin/pytest tests/test_custom_widgets_register.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement `patchfeld/layout/custom_widgets.py`**

```python
from textual.widget import Widget

from patchfeld.layout.registry import WidgetRegistry


class CustomWidgetError(Exception):
    """Raised when a custom-widget source can't be exec'd or doesn't yield
    a usable Widget subclass."""


def register_custom_widget(
    registry: WidgetRegistry,
    name: str,
    source: str,
    *,
    description: str = "",
    props_schema: dict | None = None,
) -> None:
    """Exec `source` in an isolated namespace and register the resulting
    Widget subclass under `name`.

    Class detection:
      1. `WIDGET_CLASS = SomeClass` sentinel in the namespace.
      2. A class named exactly `name`.
      3. A single Widget subclass defined in the source.
      Otherwise CustomWidgetError.

    The namespace is seeded with no special imports — the source is
    expected to import what it needs from `textual.*` and stdlib.
    """
    namespace: dict = {}
    try:
        exec(source, namespace)  # noqa: S102 - intentional, in-process trust model
    except Exception as e:
        raise CustomWidgetError(f"failed to exec source for {name!r}: {e}") from e

    cls = _find_widget_class(namespace, name)
    if cls is None:
        raise CustomWidgetError(
            f"no Widget subclass found in source for {name!r}"
        )

    # Drop any prior registration so the new class is the live one.
    registry.unregister(name)
    registry.register(
        name, cls,
        description=description,
        props_schema=props_schema or {},
    )


def _find_widget_class(namespace: dict, name: str) -> type[Widget] | None:
    sentinel = namespace.get("WIDGET_CLASS")
    if isinstance(sentinel, type) and issubclass(sentinel, Widget):
        return sentinel

    by_name = namespace.get(name)
    if isinstance(by_name, type) and issubclass(by_name, Widget):
        return by_name

    candidates: list[type[Widget]] = []
    for value in namespace.values():
        if isinstance(value, type) and issubclass(value, Widget) and value is not Widget:
            # Only count classes DEFINED in this exec, not imported ones.
            if value.__module__ == "builtins" or "<module>" not in str(value):
                # Fallback: take any Widget subclass — exec sets __module__
                # to "builtins" by default, so this is the right filter.
                pass
            candidates.append(value)

    # Deduplicate (imports can show up multiple times).
    unique = list({id(c): c for c in candidates}.values())

    if len(unique) == 1:
        return unique[0]
    if len(unique) > 1:
        raise CustomWidgetError(
            f"ambiguous: source defined {len(unique)} Widget subclasses; "
            f"set WIDGET_CLASS = ... or name one class {name!r} to disambiguate"
        )
    return None
```

The `_find_widget_class` heuristic for detecting "classes DEFINED in this exec" is fragile. The cleanest test is `cls.__module__ == "builtins"` — Python's `exec` with no `__name__` in globals makes new classes' `__module__` default to `"builtins"`. That filters out imports cleanly. Adjust the implementation if that filter doesn't behave as documented in your test runs.

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest tests/test_custom_widgets_register.py -v
.venv/bin/pytest -q
```

Expected: 7 new pass; full suite green (205 + 1 skipped).

If `test_register_raises_when_multiple_widget_subclasses_and_no_sentinel` doesn't trigger the "ambiguous" path because the heuristic incorrectly counts imported `Static` as a "defined" class, narrow the filter. The cleanest version:

```python
candidates = [
    v for v in namespace.values()
    if isinstance(v, type) and issubclass(v, Widget)
    and v is not Widget
    and v.__module__ == "builtins"  # exec'd classes get this default
]
```

- [ ] **Step 5: Commit**

```bash
git add patchfeld/layout/custom_widgets.py tests/test_custom_widgets_register.py
git commit -m "feat(layout): register_custom_widget exec sandbox for mode-C widgets"
```

---

## Task 3 — `_set_layout_handler` walks `custom_widgets`

**Files:**
- Modify: `patchfeld/orchestrator/tools.py`
- Test: `tests/test_orchestrator_tools_custom_widgets.py`

The orchestrator's `set_layout` tool now needs to register any `custom_widgets` from the spec BEFORE the layout is applied. The handler needs a `widget_registry` reference so it can call `register_custom_widget`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_orchestrator_tools_custom_widgets.py`:

```python
import pytest

from patchfeld.agents.fake_sdk_adapter import FakeSDKAdapter
from patchfeld.agents.manager import AgentManager
from patchfeld.events import EventBus
from patchfeld.layout.registry import WidgetRegistry
from patchfeld.layout.spec import LayoutSpec
from patchfeld.orchestrator.tools import build_orchestrator_tools
from patchfeld.persistence.layouts_store import NamedLayoutsStore


def _make(tmp_path, ok_script):
    return AgentManager(
        cwd=tmp_path, bus=EventBus(),
        adapter_factory=lambda: FakeSDKAdapter(scripts=[ok_script()]),
    )


@pytest.mark.asyncio
async def test_set_layout_registers_custom_widget_before_apply(tmp_path, ok_script):
    manager = _make(tmp_path, ok_script)
    store = NamedLayoutsStore(global_dir=tmp_path)
    registry = WidgetRegistry()
    # Register the always-required OrchestratorChat (placeholder).
    from textual.widgets import Static
    registry.register("OrchestratorChat", Static)

    applied: list[LayoutSpec] = []
    async def apply_callable(spec, *, layout_name=None):
        applied.append(spec)

    tools = build_orchestrator_tools(
        manager,
        apply_layout=apply_callable,
        layouts_store=store,
        widget_registry=registry,
    )
    set_layout = tools["set_layout"]

    spec_dict = {
        "version": 1,
        "layout": {
            "type": "horizontal",
            "children": [
                {"id": "orch", "widget": "OrchestratorChat"},
                {"id": "fancy", "widget": "Fancy"},
            ],
        },
        "custom_widgets": [
            {"name": "Fancy", "source":
                "from textual.widgets import Static\n"
                "class Fancy(Static):\n"
                "    pass\n"},
        ],
    }
    out = await set_layout({"spec": spec_dict})
    assert "applied" in out["content"][0]["text"].lower()
    assert applied  # apply was called
    assert registry.get("Fancy").__name__ == "Fancy"


@pytest.mark.asyncio
async def test_set_layout_with_invalid_custom_widget_aborts(tmp_path, ok_script):
    manager = _make(tmp_path, ok_script)
    store = NamedLayoutsStore(global_dir=tmp_path)
    registry = WidgetRegistry()
    from textual.widgets import Static
    registry.register("OrchestratorChat", Static)

    applied: list = []
    async def apply_callable(spec, *, layout_name=None):
        applied.append(spec)

    tools = build_orchestrator_tools(
        manager,
        apply_layout=apply_callable,
        layouts_store=store,
        widget_registry=registry,
    )
    set_layout = tools["set_layout"]

    spec_dict = {
        "version": 1,
        "layout": {"id": "orch", "widget": "OrchestratorChat"},
        "custom_widgets": [
            {"name": "Broken", "source": "this is not valid python\n"},
        ],
    }
    out = await set_layout({"spec": spec_dict})
    text = out["content"][0]["text"].lower()
    assert "error" in text or "broken" in text
    # Apply must NOT have been called.
    assert applied == []
```

- [ ] **Step 2: Run and confirm failure**

```bash
.venv/bin/pytest tests/test_orchestrator_tools_custom_widgets.py -v
```

Expected: failures (custom_widgets not registered before apply).

- [ ] **Step 3: Modify `patchfeld/orchestrator/tools.py`**

The current `_set_layout_handler` takes only `apply_layout`. Replace its definition (and the `widget_registry` plumbing) so it also accepts the registry and walks custom_widgets:

```python
def _set_layout_handler(apply_layout, widget_registry=None):
    from patchfeld.layout.custom_widgets import register_custom_widget, CustomWidgetError

    async def set_layout_tool(args: dict) -> dict:
        try:
            spec = LayoutSpec.model_validate(args["spec"])
        except Exception as e:
            return {"content": [{"type": "text", "text": f"Invalid LayoutSpec: {e}"}]}
        # Register custom widgets BEFORE applying. If any source fails to
        # exec or doesn't yield a Widget subclass, abort the apply atomically.
        if spec.custom_widgets and widget_registry is not None:
            for cw in spec.custom_widgets:
                try:
                    register_custom_widget(widget_registry, cw.name, cw.source)
                except CustomWidgetError as e:
                    return {
                        "content": [{
                            "type": "text",
                            "text": f"Custom widget {cw.name!r} error: {e}",
                        }]
                    }
        try:
            await apply_layout(spec)
        except Exception as e:
            return {"content": [{"type": "text", "text": f"Apply error: {e}"}]}
        return {"content": [{"type": "text", "text": "Layout applied."}]}
    return set_layout_tool
```

In `build_orchestrator_tools`, change the line that adds `set_layout` to forward `widget_registry`:

```python
    if apply_layout is not None and layouts_store is not None:
        handlers["set_layout"] = _set_layout_handler(apply_layout, widget_registry)
        handlers["save_layout"] = _save_layout_handler(layouts_store)
        handlers["load_layout"] = _load_layout_handler(apply_layout, layouts_store)
        handlers["list_layouts"] = _list_layouts_handler(layouts_store)
```

In `build_orchestrator_mcp_server`, mirror the change for the SDK-server-side `set_layout`:

```python
        layout_specs = [
            ("set_layout",
             "Replace the current UI layout with the given LayoutSpec dict. "
             "If `spec.custom_widgets` is present, each entry is exec'd to "
             "register a new Widget class before the layout is applied.",
             {"spec": dict},
             _set_layout_handler(apply_layout, widget_registry)),
            ...rest unchanged...
        ]
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest tests/test_orchestrator_tools_custom_widgets.py tests/test_orchestrator_tools_layout.py -v
.venv/bin/pytest -q
```

Expected: 2 new pass; existing layout-tools tests still pass; full suite green (207 + 1 skipped).

- [ ] **Step 5: Commit**

```bash
git add patchfeld/orchestrator/tools.py tests/test_orchestrator_tools_custom_widgets.py
git commit -m "feat(orchestrator): set_layout walks spec.custom_widgets and exec-registers"
```

---

## Task 4 — End-to-end mode-C smoke

**Files:**
- Create: `tests/test_app_smoke_plan6.py`

Drives a `set_layout` call via the orchestrator's tool that includes a `custom_widgets` block. Verifies the new widget shows up in the registry and is mountable from the spec.

- [ ] **Step 1: Write the test**

Create `tests/test_app_smoke_plan6.py`:

```python
import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

from patchfeld.agents.fake_sdk_adapter import FakeSDKAdapter
from patchfeld.agents.manager import AgentManager
from patchfeld.app import PatchfeldApp
from patchfeld.events import EventBus
from patchfeld.orchestrator.session import OrchestratorSession
from patchfeld.orchestrator.tools import build_orchestrator_tools


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
async def test_orchestrator_can_declare_and_use_custom_widget(tmp_path):
    bus = EventBus()
    manager = AgentManager(
        cwd=tmp_path, bus=bus,
        adapter_factory=lambda: FakeSDKAdapter(scripts=[_ok()]),
    )
    app = PatchfeldApp(cwd=tmp_path, manager=manager, global_dir=tmp_path)
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
                    {"id": "banner", "size": "40%", "widget": "Banner"},
                ],
            },
            "custom_widgets": [{
                "name": "Banner",
                "source":
                    "from textual.widgets import Static\n"
                    "class Banner(Static):\n"
                    "    def __init__(self):\n"
                    "        super().__init__('Hello from a custom widget!')\n",
            }],
            "focus": "orch",
        }
        out = await tools["set_layout"]({"spec": spec})
        assert "applied" in out["content"][0]["text"].lower()
        await pilot.pause()

        # Banner is now registered AND mounted.
        cls = app.registry.get("Banner")
        assert cls.__name__ == "Banner"

        from textual.widgets import Static
        # Find the Banner widget mounted in the panel area by id.
        panel = app.query_one("#panel-banner")
        assert panel is not None
```

- [ ] **Step 2: Run**

```bash
.venv/bin/pytest tests/test_app_smoke_plan6.py -v
.venv/bin/pytest -q
```

Expected: 1 new pass; full suite green (208 + 1 skipped).

- [ ] **Step 3: Commit**

```bash
git add tests/test_app_smoke_plan6.py
git commit -m "test(app): plan-6 e2e — orchestrator declares + uses a custom widget"
```

---

## Task 5 — Add `ptyprocess` and `pyte` dependencies

**Files:**
- Modify: `pyproject.toml`

The Terminal widget (Task 6) needs both. Pure Python on POSIX; should install cleanly.

- [ ] **Step 1: Attempt install**

```bash
cd /Users/jimmy.mills/Developer/patchfeld
uv pip install ptyprocess pyte
```

Expected: clean install. If anything fails, STOP and report.

- [ ] **Step 2: Verify imports**

```bash
.venv/bin/python -c "
import ptyprocess
import pyte
print('ptyprocess:', ptyprocess.__version__ if hasattr(ptyprocess, '__version__') else 'unknown')
print('pyte:', getattr(pyte, '__version__', 'unknown'))
print('PtyProcessUnicode:', ptyprocess.PtyProcessUnicode)
print('Screen:', pyte.Screen)
print('ByteStream:', pyte.ByteStream)
"
```

Expected: prints version strings and the three class objects.

If any class is missing under those names, note the actual paths and adjust Task 6 accordingly.

- [ ] **Step 3: Add deps to `pyproject.toml`**

In the `[project] dependencies` block:

```toml
dependencies = [
  "textual>=0.80",
  "pydantic>=2.6",
  "claude-agent-sdk>=0.1",
  "tomli-w>=1.0",
  "ptyprocess>=0.7",
  "pyte>=0.8",
]
```

(Use whatever floor your install reported.)

- [ ] **Step 4: Sync + suite green**

```bash
uv pip install -e ".[dev]"
.venv/bin/pytest -q
```

Expected: full suite green.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml
git commit -m "chore: add ptyprocess + pyte deps for the Terminal widget"
```

---

## Task 6 — Terminal widget

**Files:**
- Create: `patchfeld/widgets/terminal.py`
- Test: `tests/test_widget_terminal.py`

A real PTY widget. Spawns a subprocess via `ptyprocess.PtyProcessUnicode`, feeds its output through `pyte` for ANSI emulation, renders the screen state to a `Static` on a 50ms tick, and forwards keystrokes back to the PTY. Props: `command: list[str]` (default `[$SHELL]`), `cwd: str`, `env: dict`.

The full PTY-driven Terminal is genuinely complex. We ship the minimum that's actually useful: spawn-render-type loop with line-mode keystroke forwarding. Cursor blinking, mouse, resize-on-the-fly are out of scope.

- [ ] **Step 1: Write the failing test**

Create `tests/test_widget_terminal.py`:

```python
import os

import pytest
from textual.app import App

from patchfeld.widgets.terminal import Terminal


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
        assert term._pty is not None
        # Cleanup
        term._teardown()


@pytest.mark.asyncio
async def test_terminal_mounts_with_custom_command(tmp_path):
    # /bin/cat reads from PTY and echoes back; safe for testing on POSIX.
    app = _Host(command=["/bin/cat"], cwd=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        term = app.query_one(Terminal)
        assert term._pty is not None
        term._teardown()


@pytest.mark.asyncio
async def test_terminal_renders_subprocess_output(tmp_path):
    # echo writes a known string and exits.
    app = _Host(command=["/bin/sh", "-c", "echo hello-from-pty"])
    async with app.run_test() as pilot:
        await pilot.pause()
        term = app.query_one(Terminal)
        # Pump the read loop a few times to consume "hello-from-pty\n".
        for _ in range(20):
            term._tick()
            await pilot.pause()
        # The screen's display contains the echoed string.
        text = "\n".join(term._screen.display)
        assert "hello-from-pty" in text
        term._teardown()
```

- [ ] **Step 2: Run and confirm failure**

```bash
.venv/bin/pytest tests/test_widget_terminal.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement `patchfeld/widgets/terminal.py`**

```python
import os

from textual.app import ComposeResult
from textual.containers import Container
from textual.widgets import Static

import ptyprocess
import pyte


def _default_command() -> list[str]:
    return [os.environ.get("SHELL", "/bin/sh")]


class Terminal(Container):
    """Real PTY hosted in a Textual panel.

    Spawns a subprocess via ptyprocess.PtyProcessUnicode, feeds output
    through pyte for ANSI emulation, and renders the screen on a 50ms
    poll. Anything typed here is OPAQUE to the orchestrator (intentional
    escape-hatch behavior — use this for an interactive `claude` CLI
    session inside patchfeld).

    Props:
      command: argv list (default: [$SHELL])
      cwd: working directory (default: process cwd)
      env: extra env vars merged into os.environ

    Limitations: line-mode keystroke forwarding only; no mouse; resize
    on the fly is best-effort. POSIX-only (ptyprocess).
    """

    DEFAULT_CSS = """
    Terminal {
        border: round $surface-lighten-2;
        padding: 0 1;
        background: black;
        color: white;
    }
    Terminal Static {
        background: black;
    }
    """

    DEFAULT_COLS = 80
    DEFAULT_ROWS = 24

    def __init__(
        self,
        *,
        command: list[str] | None = None,
        cwd: str | None = None,
        env: dict | None = None,
    ) -> None:
        super().__init__()
        self._command = command or _default_command()
        self._cwd = cwd
        environ = dict(os.environ)
        if env:
            environ.update(env)
        self._env = environ
        self._pty = None
        self._screen = pyte.Screen(self.DEFAULT_COLS, self.DEFAULT_ROWS)
        self._stream = pyte.ByteStream(self._screen)
        self._timer = None

    def compose(self) -> ComposeResult:
        yield Static("", id="terminal-screen")

    def on_mount(self) -> None:
        try:
            self._pty = ptyprocess.PtyProcessUnicode.spawn(
                self._command,
                cwd=self._cwd,
                env=self._env,
                dimensions=(self.DEFAULT_ROWS, self.DEFAULT_COLS),
            )
        except Exception as e:
            self._show_error(f"PTY spawn failed: {e}")
            return
        self._timer = self.set_interval(0.05, self._tick)

    def on_unmount(self) -> None:
        self._teardown()

    def _teardown(self) -> None:
        if self._timer is not None:
            try:
                self._timer.stop()
            except Exception:
                pass
            self._timer = None
        if self._pty is not None:
            try:
                self._pty.close(force=True)
            except Exception:
                pass
            self._pty = None

    def _tick(self) -> None:
        if self._pty is None:
            return
        try:
            # PtyProcessUnicode.read() blocks; use isalive() to gate, and
            # nonblocking read by reading 1024 bytes at a time. ptyprocess
            # raises EOFError when the child exits.
            chunk = self._pty.read(1024)
        except EOFError:
            self._teardown()
            return
        except Exception:
            return
        if chunk:
            # ByteStream wants bytes; PtyProcessUnicode gives us str.
            self._stream.feed(chunk.encode("utf-8", errors="replace"))
            self._refresh()

    def _refresh(self) -> None:
        from rich.text import Text
        try:
            screen = self.query_one("#terminal-screen", Static)
        except Exception:
            return
        text = Text("\n".join(self._screen.display))
        screen.update(text)

    def _show_error(self, msg: str) -> None:
        from rich.text import Text
        try:
            self.query_one("#terminal-screen", Static).update(Text(msg))
        except Exception:
            pass

    def on_key(self, event) -> None:
        # Forward printable characters and a small set of control keys to
        # the PTY. Don't claim arrow keys / function keys yet — line-mode
        # forwarding is enough to use a shell.
        if self._pty is None:
            return
        key = event.key
        char = event.character
        try:
            if char is not None and len(char) == 1 and char.isprintable():
                self._pty.write(char)
                event.stop()
            elif key == "enter":
                self._pty.write("\n")
                event.stop()
            elif key == "backspace":
                self._pty.write("\x7f")
                event.stop()
            elif key == "tab":
                self._pty.write("\t")
                event.stop()
            elif key == "ctrl+c":
                self._pty.write("\x03")
                event.stop()
            elif key == "ctrl+d":
                self._pty.write("\x04")
                event.stop()
        except Exception:
            pass
```

The `Terminal.read(1024)` call may block on PtyProcessUnicode. If the test hangs, switch to `nonblocking_read` via `select` — set the fd to nonblocking and check `select.select([self._pty.fd], [], [], 0)` before reading. Apply if needed.

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest tests/test_widget_terminal.py -v
.venv/bin/pytest -q
```

Expected: 3 new pass; full suite green (211 + 1 skipped).

If `test_terminal_renders_subprocess_output` fails because the PTY read hasn't drained the echoed text in 20 ticks (each tick is one read at most), increase the tick count. If it fails because `read` blocks indefinitely, switch to nonblocking I/O (described above).

- [ ] **Step 5: Commit**

```bash
git add patchfeld/widgets/terminal.py tests/test_widget_terminal.py
git commit -m "feat(widgets): Terminal — ptyprocess + pyte PTY widget"
```

---

## Task 7 — Register Terminal in default registry

**Files:**
- Modify: `patchfeld/app.py`

- [ ] **Step 1: Add import + registry entry**

In `patchfeld/app.py`, add the import at the top:

```python
from patchfeld.widgets.terminal import Terminal
```

In `build_default_registry`, append a Terminal registration:

```python
    reg.register(
        "Terminal", Terminal,
        description=(
            "Real PTY in a panel. Use this for an interactive `claude` CLI "
            "session inside patchfeld — anything typed here is OPAQUE to the "
            "orchestrator (intentional escape-hatch behavior). Optional "
            "`command` (argv), `cwd`, and `env` props."
        ),
        props_schema={"command": list, "cwd": str, "env": dict},
    )
```

- [ ] **Step 2: Smoke**

```bash
cd /Users/jimmy.mills/Developer/patchfeld && .venv/bin/python -c "
from patchfeld.app import build_default_registry
reg = build_default_registry()
assert 'Terminal' in reg.known()
print('Terminal registered:', reg.get('Terminal').__name__)
"
.venv/bin/pytest -q
```

Expected: prints "Terminal registered: Terminal"; full suite green.

- [ ] **Step 3: Commit**

```bash
git add patchfeld/app.py
git commit -m "feat(app): register Terminal widget in default registry"
```

---

## Task 8 — Carry-over: LogTail handles file rotation

**Files:**
- Modify: `patchfeld/widgets/log_tail.py`
- Test: `tests/test_widget_log_tail_rotation.py`

`LogTail` keeps `self._fp` open for the widget's life. If the file is rotated/deleted/recreated externally, `_fp` returns empty reads forever. Detect this by checking the inode each tick and re-opening when it changes.

- [ ] **Step 1: Write the failing test**

Create `tests/test_widget_log_tail_rotation.py`:

```python
from pathlib import Path

import pytest
from textual.app import App

from patchfeld.widgets.log_tail import LogTail


class _Host(App):
    def __init__(self, file_path: str):
        super().__init__()
        self._file_path = file_path

    def compose(self):
        yield LogTail(file_path=self._file_path)


@pytest.mark.asyncio
async def test_log_tail_reopens_after_rotation(tmp_path: Path):
    p = tmp_path / "x.log"
    p.write_text("first\n")

    app = _Host(str(p))
    async with app.run_test() as pilot:
        await pilot.pause()
        tail = app.query_one(LogTail)
        assert "first" in tail.text

        # Rotate: rename the existing file and create a fresh one with the
        # same name. The widget should detect the inode change on the next
        # tick and reopen.
        rotated = tmp_path / "x.log.1"
        p.rename(rotated)
        p.write_text("after rotation\n")

        # A few ticks to detect + reopen + read.
        for _ in range(5):
            tail._tick()
            await pilot.pause()

        assert "after rotation" in tail.text
```

- [ ] **Step 2: Run and confirm failure**

```bash
.venv/bin/pytest tests/test_widget_log_tail_rotation.py -v
```

Expected: failure — the existing impl reads from the old `_fp` (now pointing at the rotated file, which has no new bytes).

- [ ] **Step 3: Modify `patchfeld/widgets/log_tail.py`**

Add inode tracking in `on_mount` and a re-open path in `_tick`. Replace the relevant methods:

```python
    def on_mount(self) -> None:
        if not self._path.exists():
            self.text = f"File not found: {self._path}"
            self._update_static()
            return
        try:
            lines = self._path.read_text(encoding="utf-8", errors="replace").splitlines()
            self.text = "\n".join(lines[-self._tail_lines:])
        except Exception as e:
            self.text = f"Error reading {self._path}: {e}"
            self._update_static()
            return
        self._update_static()
        self._open_at_end()
        self._timer = self.set_interval(0.25, self._tick)

    def _open_at_end(self) -> None:
        try:
            self._fp = self._path.open("r", encoding="utf-8", errors="replace")
            self._fp.seek(0, 2)
            self._inode = self._path.stat().st_ino
        except Exception:
            self._fp = None
            self._inode = None

    def _tick(self) -> None:
        # Detect rotation: if the file's inode changed (or it disappeared
        # and a new one took its place), close the old fp and reopen.
        try:
            current_inode = self._path.stat().st_ino if self._path.exists() else None
        except Exception:
            current_inode = None
        if current_inode != getattr(self, "_inode", None):
            if self._fp is not None:
                try:
                    self._fp.close()
                except Exception:
                    pass
                self._fp = None
            if current_inode is not None:
                self._open_at_end()
                # After rotation, read from the start of the new file so
                # the user doesn't miss the first lines.
                if self._fp is not None:
                    try:
                        self._fp.seek(0, 0)
                    except Exception:
                        pass

        if self._fp is None:
            return
        new = self._fp.read()
        if not new:
            return
        self.text = (self.text + "\n" + new).strip("\n")
        self._update_static()
        self.scroll_end(animate=False)
```

(Keep the `_update_static` and `on_unmount` methods as-is.)

Add `self._inode = None` to `__init__` alongside `self._fp = None`:

```python
    def __init__(self, *, file_path: str, tail_lines: int = 200) -> None:
        super().__init__()
        self._path = Path(file_path)
        self._tail_lines = tail_lines
        self._fp = None
        self._inode = None
        self.text = ""
        self._timer = None
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest tests/test_widget_log_tail_rotation.py tests/test_widget_log_tail.py -v
.venv/bin/pytest -q
```

Expected: new test passes; existing log-tail tests still pass; full suite green.

- [ ] **Step 5: Commit**

```bash
git add patchfeld/widgets/log_tail.py tests/test_widget_log_tail_rotation.py
git commit -m "fix(widgets): LogTail reopens on inode change to survive file rotation"
```

---

## Task 9 — Carry-over: FileViewer docstring note about file size

**Files:**
- Modify: `patchfeld/widgets/file_viewer.py`

`FileViewer.__init__` reads the entire file into memory at mount time. The plan-5 reviewer flagged this as worth a docstring note: "use LogTail for files >1MB". Trivial change.

- [ ] **Step 1: Edit the FileViewer class docstring**

In `patchfeld/widgets/file_viewer.py`, replace the class docstring:

```python
class FileViewer(TextArea):
    """Read-only file display with extension-based syntax highlighting.

    Loads the entire file into memory at mount time — fine for typical
    source files, but for log-sized content (>~1MB) prefer the LogTail
    widget which streams from the end and polls for additions.

    If `follow_selection=True`, subscribes to `FileSelected` events on the
    EventBus and reloads to show the selected file. Pair with a `FileTree`
    panel to get a click-a-file → see-its-content workflow:

        {"id": "tree",   "widget": "FileTree",   "props": {"path": "."}}
        {"id": "viewer", "widget": "FileViewer", "props": {"follow_selection": true}}
    """
```

- [ ] **Step 2: Run tests**

```bash
.venv/bin/pytest tests/test_widget_file_viewer.py -v
.venv/bin/pytest -q
```

Expected: 3 file-viewer tests pass; full suite still green.

- [ ] **Step 3: Commit**

```bash
git add patchfeld/widgets/file_viewer.py
git commit -m "docs(widgets): FileViewer docstring notes the in-memory load + LogTail alternative"
```

---

## Task 10 — Carry-over: tighten DiffViewer empty-state assertion

**Files:**
- Modify: `tests/test_widget_diff_viewer.py`

The plan-5 reviewer flagged `test_diff_viewer_no_inputs_renders_empty_message` asserts `viewer.diff_text == "" or "no diff" in viewer.diff_text.lower()`. The `or` branch is dead — `diff_text` is empty when no inputs are passed; the placeholder is rendered, not stored. Tighten to the actual assertion.

- [ ] **Step 1: Replace the empty-state test in `tests/test_widget_diff_viewer.py`**

Find the existing test and replace it with:

```python
@pytest.mark.asyncio
async def test_diff_viewer_no_inputs_renders_empty_message():
    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        viewer = app.query_one(DiffViewer)
        # diff_text is the source-of-truth attribute; it's empty when no
        # inputs were provided. The placeholder ("No diff to display") is
        # rendered as a Static child but not stored back into diff_text.
        assert viewer.diff_text == ""
```

- [ ] **Step 2: Run tests**

```bash
.venv/bin/pytest tests/test_widget_diff_viewer.py -v
.venv/bin/pytest -q
```

Expected: 3 diff-viewer tests pass; full suite green.

- [ ] **Step 3: Commit**

```bash
git add tests/test_widget_diff_viewer.py
git commit -m "test(widgets): tighten DiffViewer empty-state assertion"
```

---

## Task 11 — Manual launch verification + tag `plan-6-complete`

- [ ] **Step 1: Imports**

```bash
cd /Users/jimmy.mills/Developer/patchfeld && .venv/bin/python -c "
from patchfeld.app import PatchfeldApp, build_default_registry
from patchfeld.layout.custom_widgets import register_custom_widget, CustomWidgetError
from patchfeld.widgets.terminal import Terminal
print('plan 6 imports OK')
reg = build_default_registry()
expected = {'OrchestratorChat', 'AgentTable', 'ActivityFeed', 'Markdown',
            'FileViewer', 'FileTree', 'DiffViewer', 'LogTail', 'Notebook',
            'Terminal'}
got = set(reg.known())
print('default registry has all expected widgets:', expected <= got)
"
```

Expected: `plan 6 imports OK` plus `default registry has all expected widgets: True`.

- [ ] **Step 2: Full suite green**

```bash
.venv/bin/pytest -v 2>&1 | tail -5
```

Expected: every non-skipped test passes; the real-SDK smoke is the only skip (1 skipped).

- [ ] **Step 3: Verify on-disk state**

```bash
git status
```

Plan doc was committed earlier. The expected leftover untracked file is `uv.lock`; don't add it.

- [ ] **Step 4: Tag**

```bash
git tag plan-6-complete
git tag --list
```

Expected: tag list includes all six milestone tags.

---

## Self-review notes (for the writer of this plan, already verified)

- **Spec coverage (final):** mode-C custom widgets (Tasks 1-4), Terminal widget (Tasks 5-7), plan-5 carry-overs (Tasks 8-10), tag (Task 11). Mode-C ships in-process trust + try/except per the brainstorming spec; subprocess sandboxing remains parking-lot.
- **Placeholder scan:** no "TODO" / "TBD" / "implement later". Every step has actual code or commands. Risk areas explicitly call STOP-and-report.
- **Type consistency:** `register_custom_widget`, `CustomWidgetError`, `WIDGET_CLASS` sentinel, `Terminal`, `_pty`, `_screen`, `_stream`, `_inode` — names used identically across all tasks.
- **Risk areas:**
  - Task 2: `_find_widget_class` heuristic for "defined here vs imported" relies on `cls.__module__ == "builtins"` for exec'd classes. If that assumption breaks, the ambiguity test misfires.
  - Task 5/6: `ptyprocess` is POSIX-only. The Terminal widget will refuse to install on Windows. Documented as non-goal.
  - Task 6: `PtyProcessUnicode.read()` may block. The plan suggests switching to `select`-based nonblocking if hangs occur.
  - Task 8: inode tracking works on POSIX. On Windows, `st_ino` is unreliable; LogTail rotation detection would need a different strategy. Out of scope (Windows is not a target).
- **Tool count after plan 6:** orchestrator unchanged at ~18 MCP tools. WidgetRegistry now has 10 default widgets + arbitrary mode-C extensions.

This is the final implementation plan. After plan 6 the spec is fully realized: agent-mutable layout + config + bindings + history + 10 widgets + custom widget escape hatch + real PTY.
