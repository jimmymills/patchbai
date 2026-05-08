# Panel Border Titles Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render a human-readable title in the top border of every mounted panel, with prop-aware defaults, agent override via `Panel.title`, and a `get_layout` MCP tool so the agent can resolve user references like "the Activity Panel" back to a panel `id`.

**Architecture:** One new optional field on `Panel` (`title: str | None`). One shared resolver `resolve_title(panel, widget_cls)` in a new `patchfeld/layout/titles.py` that the layout engine and the new `get_layout` tool both call. Per-widget defaults expressed as either a `DEFAULT_BORDER_TITLE` class attribute (static) or a `default_border_title(cls, props)` classmethod (prop-aware). The engine assigns `widget.border_title` at mount; the five borderless widgets gain a `border:` rule in their DEFAULT_CSS so titles render; a heuristic safety net catches custom widgets without a border.

**Tech Stack:** Python 3.12, Textual ≥ 0.87 (for `border_title`), Pydantic 2, pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-05-06-panel-border-titles-design.md`

---

## File Structure

**Create:**
- `patchfeld/layout/titles.py` — `resolve_title()` helper + `populate_effective_titles()` walk used by `get_layout`.
- `tests/test_layout_titles_resolver.py` — pure resolver tests (no Textual app harness).
- `tests/test_layout_engine_titles.py` — engine integration tests for `border_title` + safety-net border.
- `tests/test_orchestrator_tools_get_layout.py` — `get_layout` handler tests.

**Modify:**
- `patchfeld/layout/spec.py` — add `Panel.title` field.
- `patchfeld/layout/engine.py` — use `resolve_title` in `_build`, apply safety-net border.
- `patchfeld/widgets/orchestrator_chat.py`, `agent_table.py`, `placeholders.py`, `diff_viewer.py` — add `DEFAULT_BORDER_TITLE`.
- `patchfeld/widgets/file_tree.py`, `file_viewer.py`, `markdown.py`, `log_tail.py`, `notebook.py`, `terminal.py`, `agent_transcript.py`, `rich_transcript.py` — add `default_border_title` classmethod.
- `patchfeld/widgets/markdown.py`, `file_tree.py`, `notebook.py`, `file_viewer.py`, `rich_transcript.py` — add `border:` to DEFAULT_CSS.
- `patchfeld/orchestrator/tools.py` — new `_get_layout_handler` + wire it into `build_orchestrator_tools` and `build_orchestrator_mcp_server`. Update `set_layout` description.
- `patchfeld/orchestrator/session.py` — accept `current_layout` callable, forward to MCP builders.
- `patchfeld/app.py` — pass `current_layout=lambda: self._current_spec` into `OrchestratorSession`.

---

## Task 1: Add `Panel.title` to the layout spec

**Files:**
- Modify: `patchfeld/layout/spec.py:6-13`
- Test: `tests/test_layout_spec.py` (new test added at end of file)

- [ ] **Step 1: Read existing test file**

Run: `cat tests/test_layout_spec.py | head -40`
Skim it so the new test matches house style (imports, naming).

- [ ] **Step 2: Write the failing test**

Append to `tests/test_layout_spec.py`:

```python
def test_panel_accepts_optional_title():
    panel = Panel.model_validate({"id": "feed", "widget": "ActivityFeed", "title": "Activity"})
    assert panel.title == "Activity"


def test_panel_title_defaults_to_none():
    panel = Panel.model_validate({"id": "feed", "widget": "ActivityFeed"})
    assert panel.title is None


def test_panel_title_round_trips_through_dump():
    panel = Panel.model_validate({"id": "feed", "widget": "ActivityFeed", "title": "Activity"})
    dumped = panel.model_dump(mode="json")
    assert dumped["title"] == "Activity"
    reparsed = Panel.model_validate(dumped)
    assert reparsed == panel


def test_panel_extra_fields_still_rejected_with_title_present():
    with pytest.raises(Exception):
        Panel.model_validate({"id": "feed", "widget": "ActivityFeed", "title": "Activity", "junk": 1})
```

If `pytest` is not already imported at the top of the file, add `import pytest`.

- [ ] **Step 3: Run tests, expect failure**

Run: `uv run pytest tests/test_layout_spec.py::test_panel_accepts_optional_title -v`
Expected: FAIL — `Object has no attribute 'title'` or `extra fields not permitted`.

- [ ] **Step 4: Add the field**

Edit `patchfeld/layout/spec.py`, replacing the `Panel` class body:

```python
class Panel(BaseModel):
    """A leaf node — one widget instance."""
    model_config = ConfigDict(extra="forbid")

    id: str
    widget: str
    props: dict = Field(default_factory=dict)
    size: str | None = None
    title: str | None = None
```

- [ ] **Step 5: Run the new tests, expect pass**

Run: `uv run pytest tests/test_layout_spec.py -v`
Expected: PASS for all four new tests, no regressions in existing tests.

- [ ] **Step 6: Commit**

```bash
git add patchfeld/layout/spec.py tests/test_layout_spec.py
git commit -m "feat(layout): add optional Panel.title field"
```

---

## Task 2: Title resolver helper + static defaults

**Files:**
- Create: `patchfeld/layout/titles.py`
- Modify: `patchfeld/widgets/orchestrator_chat.py`, `patchfeld/widgets/agent_table.py`, `patchfeld/widgets/placeholders.py`, `patchfeld/widgets/diff_viewer.py`
- Test: `tests/test_layout_titles_resolver.py`

- [ ] **Step 1: Write the failing resolver test**

Create `tests/test_layout_titles_resolver.py`:

```python
from patchfeld.layout.spec import Panel
from patchfeld.layout.titles import resolve_title
from patchfeld.widgets.agent_table import AgentTable
from patchfeld.widgets.diff_viewer import DiffViewer
from patchfeld.widgets.orchestrator_chat import OrchestratorChat
from patchfeld.widgets.placeholders import ActivityFeed


class _Bare:
    """Plain class — no DEFAULT_BORDER_TITLE, no default_border_title method."""


def test_explicit_panel_title_wins_over_widget_default():
    p = Panel(id="x", widget="OrchestratorChat", title="Custom")
    assert resolve_title(p, OrchestratorChat) == "Custom"


def test_static_default_border_title_used_when_panel_title_missing():
    assert resolve_title(Panel(id="orch", widget="OrchestratorChat"), OrchestratorChat) == "Orchestrator"
    assert resolve_title(Panel(id="agents", widget="AgentTable"), AgentTable) == "Agents"
    assert resolve_title(Panel(id="feed", widget="ActivityFeed"), ActivityFeed) == "Activity"
    assert resolve_title(Panel(id="diff", widget="DiffViewer"), DiffViewer) == "Diff"


def test_class_name_is_last_resort_fallback():
    assert resolve_title(Panel(id="x", widget="Bare"), _Bare) == "_Bare"


def test_resolver_swallows_classmethod_exceptions():
    class Boom:
        @classmethod
        def default_border_title(cls, props):
            raise RuntimeError("nope")
    assert resolve_title(Panel(id="x", widget="Boom"), Boom) == "Boom"
```

- [ ] **Step 2: Run, expect ImportError on `patchfeld.layout.titles`**

Run: `uv run pytest tests/test_layout_titles_resolver.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'patchfeld.layout.titles'`.

- [ ] **Step 3: Create the resolver module**

Create `patchfeld/layout/titles.py`:

```python
from typing import Any

from patchfeld.layout.spec import Container, Panel


def resolve_title(panel: Panel | dict, widget_cls: type) -> str:
    """Resolve the effective border title for a panel.

    Resolution order:
      1. ``panel.title`` if explicitly set.
      2. ``widget_cls.default_border_title(props)`` classmethod if defined.
      3. ``widget_cls.DEFAULT_BORDER_TITLE`` class attribute if defined.
      4. ``widget_cls.__name__`` as a last-resort fallback.

    Any exception raised inside ``default_border_title`` is swallowed and the
    resolution falls through to step 3 / step 4. A bad widget must never abort
    layout apply.

    ``panel`` may be either a ``Panel`` model or a plain dict from
    ``model_dump`` (so ``get_layout`` can call this on a dumped tree without
    re-validating).
    """
    if isinstance(panel, Panel):
        explicit = panel.title
        props = panel.props or {}
    else:
        explicit = panel.get("title")
        props = panel.get("props") or {}

    if explicit:
        return explicit

    fn = getattr(widget_cls, "default_border_title", None)
    if callable(fn):
        try:
            value = fn(props)
        except Exception:
            value = None
        if value:
            return value

    static = getattr(widget_cls, "DEFAULT_BORDER_TITLE", None)
    if static:
        return static

    return widget_cls.__name__


def populate_effective_titles(node: Any, registry) -> None:
    """Walk a dumped LayoutSpec tree and fill in each panel's effective title.

    Operates in-place on the dict returned by ``LayoutSpec.model_dump(mode='json')``.
    Used by the ``get_layout`` MCP tool so the orchestrator sees the same
    titles the user sees.
    """
    if not isinstance(node, dict):
        return
    if "widget" in node:
        # Leaf panel.
        if not node.get("title"):
            try:
                cls = registry.get(node["widget"])
            except Exception:
                node["title"] = node["widget"]
                return
            node["title"] = resolve_title(node, cls)
        return
    for child in node.get("children", []):
        populate_effective_titles(child, registry)
```

- [ ] **Step 4: Add `DEFAULT_BORDER_TITLE` to OrchestratorChat**

Edit `patchfeld/widgets/orchestrator_chat.py`. Inside `class OrchestratorChat(Vertical):`, add a class attribute right after the `AGENT_ID = "orchestrator"` line:

```python
    DEFAULT_BORDER_TITLE = "Orchestrator"
```

- [ ] **Step 5: Add `DEFAULT_BORDER_TITLE` to AgentTable**

Edit `patchfeld/widgets/agent_table.py`. Inside `class AgentTable(Container):`, add a class attribute right after the docstring (above `DEFAULT_CSS`):

```python
    DEFAULT_BORDER_TITLE = "Agents"
```

- [ ] **Step 6: Add `DEFAULT_BORDER_TITLE` to ActivityFeed**

Edit `patchfeld/widgets/placeholders.py`. Inside `class ActivityFeed(Container):`, add right after the docstring:

```python
    DEFAULT_BORDER_TITLE = "Activity"
```

- [ ] **Step 7: Add `DEFAULT_BORDER_TITLE` to DiffViewer**

Edit `patchfeld/widgets/diff_viewer.py`. Inside `class DiffViewer(VerticalScroll):`, add right after the docstring:

```python
    DEFAULT_BORDER_TITLE = "Diff"
```

- [ ] **Step 8: Run resolver tests, expect pass**

Run: `uv run pytest tests/test_layout_titles_resolver.py -v`
Expected: PASS, all five tests.

- [ ] **Step 9: Commit**

```bash
git add patchfeld/layout/titles.py tests/test_layout_titles_resolver.py \
        patchfeld/widgets/orchestrator_chat.py patchfeld/widgets/agent_table.py \
        patchfeld/widgets/placeholders.py patchfeld/widgets/diff_viewer.py
git commit -m "feat(layout): title resolver + static DEFAULT_BORDER_TITLE on 4 widgets"
```

---

## Task 3: Prop-aware `default_border_title` classmethods

**Files:**
- Modify: `patchfeld/widgets/file_tree.py`, `file_viewer.py`, `markdown.py`, `log_tail.py`, `notebook.py`, `terminal.py`, `agent_transcript.py`, `rich_transcript.py`
- Test: `tests/test_layout_titles_resolver.py` (extend)

- [ ] **Step 1: Write the failing prop-aware tests**

Append to `tests/test_layout_titles_resolver.py`:

```python
from patchfeld.layout.spec import Panel
from patchfeld.layout.titles import resolve_title
from patchfeld.widgets.agent_transcript import AgentTranscript
from patchfeld.widgets.diff_viewer import DiffViewer
from patchfeld.widgets.file_tree import FileTree
from patchfeld.widgets.file_viewer import FileViewer
from patchfeld.widgets.log_tail import LogTail
from patchfeld.widgets.markdown import Markdown
from patchfeld.widgets.notebook import Notebook
from patchfeld.widgets.rich_transcript import RichTranscript
from patchfeld.widgets.terminal import Terminal


def test_file_tree_default_title_uses_path():
    p = Panel(id="t", widget="FileTree", props={"path": "/Users/me/proj/src"})
    assert resolve_title(p, FileTree) == "Files: /Users/me/proj/src"


def test_file_tree_default_without_path_falls_through():
    p = Panel(id="t", widget="FileTree")
    assert resolve_title(p, FileTree) == "Files"


def test_file_viewer_default_uses_basename():
    p = Panel(id="v", widget="FileViewer", props={"file_path": "/a/b/c.py"})
    assert resolve_title(p, FileViewer) == "File: c.py"


def test_file_viewer_without_file_path_falls_through():
    p = Panel(id="v", widget="FileViewer")
    assert resolve_title(p, FileViewer) == "File"


def test_markdown_default_uses_basename():
    p = Panel(id="m", widget="Markdown", props={"file_path": "/a/b/README.md"})
    assert resolve_title(p, Markdown) == "Markdown: README.md"


def test_markdown_without_file_path_falls_through():
    p = Panel(id="m", widget="Markdown")
    assert resolve_title(p, Markdown) == "Markdown"


def test_log_tail_default_uses_basename():
    p = Panel(id="l", widget="LogTail", props={"file_path": "/var/log/app.log"})
    assert resolve_title(p, LogTail) == "Log: app.log"


def test_notebook_default_uses_name():
    p = Panel(id="n", widget="Notebook", props={"name": "ideas"})
    assert resolve_title(p, Notebook) == "Note: ideas"


def test_terminal_default_uses_command_basename():
    p = Panel(id="t", widget="Terminal", props={"command": ["/usr/bin/zsh", "-l"]})
    assert resolve_title(p, Terminal) == "Terminal: zsh"


def test_terminal_without_command_falls_through():
    p = Panel(id="t", widget="Terminal")
    assert resolve_title(p, Terminal) == "Terminal"


def test_agent_transcript_default_includes_agent_id():
    p = Panel(id="a", widget="AgentTranscript", props={"agent_id": "abc-123"})
    assert resolve_title(p, AgentTranscript) == "Agent: abc-123"


def test_rich_transcript_default_includes_agent_id():
    p = Panel(id="r", widget="RichTranscript", props={"agent_id": "abc-123"})
    assert resolve_title(p, RichTranscript) == "Transcript: abc-123"
```

- [ ] **Step 2: Run, expect failures**

Run: `uv run pytest tests/test_layout_titles_resolver.py -v`
Expected: FAIL — each prop-aware widget falls through to its class name (since no classmethod defined yet).

- [ ] **Step 3: Add classmethod to FileTree**

Edit `patchfeld/widgets/file_tree.py`. Replace the `class FileTree` block to add the classmethod after the existing methods (before EOF, but inside the class):

```python
    @classmethod
    def default_border_title(cls, props: dict) -> str:
        path = props.get("path")
        if path:
            return f"Files: {path}"
        return "Files"
```

- [ ] **Step 4: Add classmethod to FileViewer**

Edit `patchfeld/widgets/file_viewer.py`. Inside `class FileViewer(TextArea):`, add at the end of the class:

```python
    @classmethod
    def default_border_title(cls, props: dict) -> str:
        from pathlib import Path as _P
        file_path = props.get("file_path")
        if file_path:
            return f"File: {_P(file_path).name}"
        return "File"
```

- [ ] **Step 5: Add classmethod to Markdown**

Edit `patchfeld/widgets/markdown.py`. Inside `class Markdown(VerticalScroll):`, add at the end:

```python
    @classmethod
    def default_border_title(cls, props: dict) -> str:
        file_path = props.get("file_path")
        if file_path:
            return f"Markdown: {Path(file_path).name}"
        return "Markdown"
```

(`Path` is already imported at the top of the file.)

- [ ] **Step 6: Add classmethod to LogTail**

Edit `patchfeld/widgets/log_tail.py`. Inside `class LogTail(VerticalScroll):`, add at the end:

```python
    @classmethod
    def default_border_title(cls, props: dict) -> str:
        file_path = props.get("file_path")
        if file_path:
            return f"Log: {Path(file_path).name}"
        return "Log"
```

(`Path` is already imported.)

- [ ] **Step 7: Add classmethod to Notebook**

Edit `patchfeld/widgets/notebook.py`. Inside `class Notebook(TextArea):`, add at the end:

```python
    @classmethod
    def default_border_title(cls, props: dict) -> str:
        name = props.get("name")
        if name:
            return f"Note: {name}"
        return "Note"
```

- [ ] **Step 8: Add classmethod to Terminal**

Edit `patchfeld/widgets/terminal.py`. Inside `class Terminal(Container):`, add at the end:

```python
    @classmethod
    def default_border_title(cls, props: dict) -> str:
        from pathlib import Path as _P
        command = props.get("command")
        if command and isinstance(command, list) and len(command) > 0:
            return f"Terminal: {_P(command[0]).name}"
        return "Terminal"
```

- [ ] **Step 9: Add classmethod to AgentTranscript**

Edit `patchfeld/widgets/agent_transcript.py`. Inside `class AgentTranscript(Vertical):`, add at the end (after `rendered_text`):

```python
    @classmethod
    def default_border_title(cls, props: dict) -> str:
        agent_id = props.get("agent_id")
        if agent_id:
            return f"Agent: {agent_id}"
        return "Agent"
```

- [ ] **Step 10: Add classmethod to RichTranscript**

Edit `patchfeld/widgets/rich_transcript.py`. Inside `class RichTranscript(Vertical):`, add at the end of the class (after the existing methods):

```python
    @classmethod
    def default_border_title(cls, props: dict) -> str:
        agent_id = props.get("agent_id")
        if agent_id:
            return f"Transcript: {agent_id}"
        return "Transcript"
```

- [ ] **Step 11: Run prop-aware tests**

Run: `uv run pytest tests/test_layout_titles_resolver.py -v`
Expected: PASS, all tests including the originals from Task 2.

- [ ] **Step 12: Commit**

```bash
git add patchfeld/widgets/file_tree.py patchfeld/widgets/file_viewer.py \
        patchfeld/widgets/markdown.py patchfeld/widgets/log_tail.py \
        patchfeld/widgets/notebook.py patchfeld/widgets/terminal.py \
        patchfeld/widgets/agent_transcript.py patchfeld/widgets/rich_transcript.py \
        tests/test_layout_titles_resolver.py
git commit -m "feat(widgets): prop-aware default_border_title classmethods"
```

---

## Task 4: Add visible borders to the 5 borderless widgets

**Files:**
- Modify: `patchfeld/widgets/markdown.py`, `patchfeld/widgets/file_tree.py`, `patchfeld/widgets/notebook.py`, `patchfeld/widgets/file_viewer.py`, `patchfeld/widgets/rich_transcript.py`

These widgets currently render no outer border. Without a border, Textual silently drops `border_title`. Add a `border:` rule to each widget's DEFAULT_CSS so titles render. Use `round $surface-lighten-2` to match the in-tree convention.

- [ ] **Step 1: Add DEFAULT_CSS to Markdown (which has none today)**

Edit `patchfeld/widgets/markdown.py`. Inside `class Markdown(VerticalScroll):`, add right after the docstring (above `__init__`):

```python
    DEFAULT_CSS = """
    Markdown {
        border: round $surface-lighten-2;
        padding: 0 1;
    }
    """
```

- [ ] **Step 2: Add DEFAULT_CSS to FileTree (which has none today)**

Edit `patchfeld/widgets/file_tree.py`. Inside `class FileTree(DirectoryTree):`, add right after the docstring (above `__init__`):

```python
    DEFAULT_CSS = """
    FileTree {
        border: round $surface-lighten-2;
        padding: 0 1;
    }
    """
```

- [ ] **Step 3: Add DEFAULT_CSS to Notebook (which has none today)**

Edit `patchfeld/widgets/notebook.py`. Inside `class Notebook(TextArea):`, add right after the docstring (above `__init__`):

```python
    DEFAULT_CSS = """
    Notebook {
        border: round $surface-lighten-2;
    }
    """
```

(No `padding` — `TextArea` manages its own internal padding.)

- [ ] **Step 4: Add DEFAULT_CSS to FileViewer (which has none today)**

Edit `patchfeld/widgets/file_viewer.py`. Inside `class FileViewer(TextArea):`, add right after the docstring (above `__init__`):

```python
    DEFAULT_CSS = """
    FileViewer {
        border: round $surface-lighten-2;
    }
    """
```

- [ ] **Step 5: Extend DEFAULT_CSS on RichTranscript**

Edit `patchfeld/widgets/rich_transcript.py`. Replace the existing RichTranscript DEFAULT_CSS block (around line 269):

```python
    DEFAULT_CSS = """
    RichTranscript {
        border: round $surface-lighten-2;
        height: 1fr;
    }
    RichTranscript > VerticalScroll {
        height: 1fr;
    }
    """
```

The inner `_TurnContainer` `border-left` styling is unrelated and unaffected.

- [ ] **Step 6: Run smoke + widget tests**

Run: `uv run pytest tests/test_widget_markdown.py tests/test_widget_file_tree.py tests/test_widget_notebook.py tests/test_widget_file_viewer.py tests/test_rich_transcript.py tests/test_app_smoke.py -v`
Expected: PASS — adding a border is purely visual; no test should care unless it asserts on border styles.

- [ ] **Step 7: Commit**

```bash
git add patchfeld/widgets/markdown.py patchfeld/widgets/file_tree.py \
        patchfeld/widgets/notebook.py patchfeld/widgets/file_viewer.py \
        patchfeld/widgets/rich_transcript.py
git commit -m "feat(widgets): give the five borderless widgets a visible border"
```

---

## Task 5: Engine integration — assign `border_title` and apply safety net

**Files:**
- Modify: `patchfeld/layout/engine.py:77-91` (the `_build` function)
- Test: `tests/test_layout_engine_titles.py` (new)

The engine sets `widget.border_title` from `resolve_title(node, cls)`. For widgets whose class has no `border:` mention in DEFAULT_CSS (a heuristic — covers the unknown / orchestrator-supplied custom case), the engine applies a default round border inline so the title still renders. We use a heuristic class-level check rather than `widget.styles.has_rule("border")` because the styling cascade has not yet run at `_build` time.

- [ ] **Step 1: Write the failing engine test**

Create `tests/test_layout_engine_titles.py`:

```python
import pytest
from textual.app import App
from textual.containers import Container
from textual.widget import Widget
from textual.widgets import Static

from patchfeld.events import EventBus
from patchfeld.layout.engine import apply as apply_layout
from patchfeld.layout.registry import WidgetRegistry
from patchfeld.layout.spec import LayoutSpec
from patchfeld.widgets.agent_table import AgentTable
from patchfeld.widgets.orchestrator_chat import OrchestratorChat
from patchfeld.widgets.placeholders import ActivityFeed


class _BorderlessCustom(Static):
    """Custom widget with no border rule — should get the engine safety net."""


class _BorderedCustom(Static):
    DEFAULT_CSS = """
    _BorderedCustom {
        border: heavy red;
    }
    """


class _HostApp(App):
    def __init__(self, bus: EventBus) -> None:
        super().__init__()
        self.event_bus = bus

    def compose(self):
        yield Container(id="panel-area")


def _registry() -> WidgetRegistry:
    reg = WidgetRegistry()
    reg.register("OrchestratorChat", OrchestratorChat)
    reg.register("AgentTable", AgentTable)
    reg.register("ActivityFeed", ActivityFeed)
    reg.register("BorderlessCustom", _BorderlessCustom)
    reg.register("BorderedCustom", _BorderedCustom)
    return reg


@pytest.mark.asyncio
async def test_engine_assigns_default_border_title_from_class_attr():
    bus = EventBus()
    app = _HostApp(bus)
    async with app.run_test() as pilot:
        await pilot.pause()
        area = app.query_one("#panel-area", Container)
        spec = LayoutSpec.model_validate({
            "version": 1,
            "layout": {
                "type": "horizontal",
                "children": [
                    {"id": "orch", "widget": "OrchestratorChat"},
                    {"id": "feed", "widget": "ActivityFeed"},
                ],
            },
        })
        await apply_layout(area, spec, _registry())
        await pilot.pause()
        assert app.query_one("#panel-orch").border_title == "Orchestrator"
        assert app.query_one("#panel-feed").border_title == "Activity"


@pytest.mark.asyncio
async def test_engine_explicit_panel_title_overrides_default():
    bus = EventBus()
    app = _HostApp(bus)
    async with app.run_test() as pilot:
        await pilot.pause()
        area = app.query_one("#panel-area", Container)
        spec = LayoutSpec.model_validate({
            "version": 1,
            "layout": {
                "type": "horizontal",
                "children": [
                    {"id": "orch", "widget": "OrchestratorChat", "title": "Boss"},
                    {"id": "feed", "widget": "ActivityFeed"},
                ],
            },
        })
        await apply_layout(area, spec, _registry())
        await pilot.pause()
        assert app.query_one("#panel-orch").border_title == "Boss"


@pytest.mark.asyncio
async def test_engine_safety_net_applies_border_to_borderless_custom_widget():
    bus = EventBus()
    app = _HostApp(bus)
    async with app.run_test() as pilot:
        await pilot.pause()
        area = app.query_one("#panel-area", Container)
        spec = LayoutSpec.model_validate({
            "version": 1,
            "layout": {
                "type": "horizontal",
                "children": [
                    {"id": "orch", "widget": "OrchestratorChat"},
                    {"id": "x", "widget": "BorderlessCustom"},
                ],
            },
        })
        await apply_layout(area, spec, _registry())
        await pilot.pause()
        widget = app.query_one("#panel-x")
        # Inline border was set by the engine.
        assert widget.styles.has_rule("border_top")
        # Title still falls through to class name.
        assert widget.border_title == "_BorderlessCustom"


@pytest.mark.asyncio
async def test_engine_does_not_override_widgets_with_their_own_border():
    bus = EventBus()
    app = _HostApp(bus)
    async with app.run_test() as pilot:
        await pilot.pause()
        area = app.query_one("#panel-area", Container)
        spec = LayoutSpec.model_validate({
            "version": 1,
            "layout": {
                "type": "horizontal",
                "children": [
                    {"id": "orch", "widget": "OrchestratorChat"},
                    {"id": "x", "widget": "BorderedCustom"},
                ],
            },
        })
        await apply_layout(area, spec, _registry())
        await pilot.pause()
        widget = app.query_one("#panel-x")
        # Engine must NOT have set an inline border (the DEFAULT_CSS one wins).
        assert not widget._inline_styles.has_rule("border_top")


@pytest.mark.asyncio
async def test_engine_buggy_default_border_title_does_not_abort_apply():
    class Boom(Static):
        @classmethod
        def default_border_title(cls, props):
            raise RuntimeError("boom")

    bus = EventBus()
    app = _HostApp(bus)
    async with app.run_test() as pilot:
        await pilot.pause()
        area = app.query_one("#panel-area", Container)
        reg = _registry()
        reg.register("Boom", Boom)
        spec = LayoutSpec.model_validate({
            "version": 1,
            "layout": {
                "type": "horizontal",
                "children": [
                    {"id": "orch", "widget": "OrchestratorChat"},
                    {"id": "x", "widget": "Boom"},
                ],
            },
        })
        await apply_layout(area, spec, reg)
        await pilot.pause()
        # Apply succeeded; widget mounted; title fell back to class name.
        assert app.query_one("#panel-x").border_title == "Boom"
```

- [ ] **Step 2: Run tests, expect failures**

Run: `uv run pytest tests/test_layout_engine_titles.py -v`
Expected: FAIL — `border_title` is `None` because the engine doesn't assign it yet.

- [ ] **Step 3: Wire `resolve_title` into `_build`**

Edit `patchfeld/layout/engine.py`. Replace the `_build` function (currently lines 77-91) with:

```python
def _has_border_in_default_css(cls) -> bool:
    """Heuristic: does this class (or an ancestor) declare a border in
    DEFAULT_CSS? Used by the safety net so we don't clobber a widget's own
    border style. Walks the MRO so subclasses inherit the answer."""
    for base in cls.__mro__:
        css = getattr(base, "DEFAULT_CSS", "") or ""
        if isinstance(css, str) and ("border:" in css or "border-" in css):
            return True
    return False


def _build(node, registry) -> "TxContainer":
    if isinstance(node, Panel):
        cls = registry.get(node.widget)
        widget = cls(**node.props) if node.props else cls()
        widget.id = f"panel-{node.id}"
        widget.can_focus = True  # panels must be focusable so focus survives rebuilds
        if node.size:
            widget.styles.width = node.size if "%" in node.size or node.size.endswith("fr") else None
            widget.styles.height = None
        # Border safety net: widgets with no DEFAULT_CSS border get a default
        # one so border_title renders. Widgets with their own border keep it.
        if not _has_border_in_default_css(cls):
            widget.styles.border = ("round", "$surface-lighten-2")
        # Title resolution (never aborts the apply on a buggy widget).
        try:
            widget.border_title = resolve_title(node, cls)
        except Exception:
            widget.border_title = cls.__name__
        return widget
    box_cls = Horizontal if node.type == "horizontal" else Vertical
    box = box_cls(*[_build(c, registry) for c in node.children])
    if node.size:
        box.styles.width = node.size
    return box
```

Add the `resolve_title` import near the top of `engine.py`, alongside the existing imports:

```python
from patchfeld.layout.titles import resolve_title
```

- [ ] **Step 4: Run engine title tests**

Run: `uv run pytest tests/test_layout_engine_titles.py -v`
Expected: PASS, all five tests.

- [ ] **Step 5: Run the full layout-engine and smoke test suite**

Run: `uv run pytest tests/test_layout_engine_idempotent.py tests/test_layout_engine_diff.py tests/test_layout_engine_focus.py tests/test_layout_engine_weakref.py tests/test_app_smoke.py tests/test_app_smoke_plan2.py tests/test_app_smoke_plan3.py tests/test_app_smoke_plan4.py tests/test_app_smoke_plan5.py tests/test_app_smoke_plan6.py -v`
Expected: PASS, no regressions.

- [ ] **Step 6: Commit**

```bash
git add patchfeld/layout/engine.py tests/test_layout_engine_titles.py
git commit -m "feat(layout): engine sets border_title and applies a safety-net border"
```

---

## Task 6: `get_layout` MCP tool — plumbing + handler

**Files:**
- Modify: `patchfeld/orchestrator/tools.py` (add handler, register in both `build_orchestrator_tools` and `build_orchestrator_mcp_server`)
- Modify: `patchfeld/orchestrator/session.py` (accept `current_layout`, forward)
- Modify: `patchfeld/app.py` (pass `current_layout` to `OrchestratorSession`)
- Test: `tests/test_orchestrator_tools_get_layout.py` (new)

- [ ] **Step 1: Write the failing handler test**

Create `tests/test_orchestrator_tools_get_layout.py`:

```python
import json

import pytest

from patchfeld.agents.fake_sdk_adapter import FakeSDKAdapter
from patchfeld.agents.manager import AgentManager
from patchfeld.events import EventBus
from patchfeld.layout.defaults import dashboard_layout
from patchfeld.layout.registry import WidgetRegistry
from patchfeld.layout.spec import LayoutSpec
from patchfeld.orchestrator.tools import build_orchestrator_tools
from patchfeld.persistence.layouts_store import NamedLayoutsStore
from patchfeld.widgets.agent_table import AgentTable
from patchfeld.widgets.orchestrator_chat import OrchestratorChat
from patchfeld.widgets.placeholders import ActivityFeed


def _make_manager(tmp_path, ok_script):
    return AgentManager(
        cwd=tmp_path,
        bus=EventBus(),
        adapter_factory=lambda: FakeSDKAdapter(scripts=[ok_script()]),
    )


def _registry() -> WidgetRegistry:
    reg = WidgetRegistry()
    reg.register("OrchestratorChat", OrchestratorChat)
    reg.register("AgentTable", AgentTable)
    reg.register("ActivityFeed", ActivityFeed)
    return reg


@pytest.mark.asyncio
async def test_get_layout_returns_message_when_no_layout_applied(tmp_path, ok_script):
    manager = _make_manager(tmp_path, ok_script)
    store = NamedLayoutsStore(global_dir=tmp_path)

    async def apply_callable(spec, *, layout_name=None):
        pass

    tools = build_orchestrator_tools(
        manager,
        apply_layout=apply_callable,
        layouts_store=store,
        widget_registry=_registry(),
        current_layout=lambda: None,
    )
    out = await tools["get_layout"]({})
    assert "no layout applied" in out["content"][0]["text"].lower()


@pytest.mark.asyncio
async def test_get_layout_returns_dashboard_with_effective_titles(tmp_path, ok_script):
    manager = _make_manager(tmp_path, ok_script)
    store = NamedLayoutsStore(global_dir=tmp_path)
    spec = dashboard_layout()

    async def apply_callable(s, *, layout_name=None):
        pass

    tools = build_orchestrator_tools(
        manager,
        apply_layout=apply_callable,
        layouts_store=store,
        widget_registry=_registry(),
        current_layout=lambda: spec,
    )
    out = await tools["get_layout"]({})
    payload = json.loads(out["content"][0]["text"])
    # Walk the dumped tree and collect (id, title) pairs.
    titles: dict[str, str] = {}

    def _walk(node):
        if "widget" in node:
            titles[node["id"]] = node["title"]
            return
        for c in node["children"]:
            _walk(c)

    _walk(payload["layout"])
    assert titles == {
        "orch": "Orchestrator",
        "agents": "Agents",
        "feed": "Activity",
    }


@pytest.mark.asyncio
async def test_get_layout_preserves_explicit_panel_title(tmp_path, ok_script):
    manager = _make_manager(tmp_path, ok_script)
    store = NamedLayoutsStore(global_dir=tmp_path)
    spec = LayoutSpec.model_validate({
        "version": 1,
        "layout": {
            "type": "horizontal",
            "children": [
                {"id": "orch", "widget": "OrchestratorChat", "title": "My Boss"},
                {"id": "feed", "widget": "ActivityFeed"},
            ],
        },
    })

    async def apply_callable(s, *, layout_name=None):
        pass

    tools = build_orchestrator_tools(
        manager,
        apply_layout=apply_callable,
        layouts_store=store,
        widget_registry=_registry(),
        current_layout=lambda: spec,
    )
    out = await tools["get_layout"]({})
    payload = json.loads(out["content"][0]["text"])
    titles = {}

    def _walk(node):
        if "widget" in node:
            titles[node["id"]] = node["title"]
            return
        for c in node["children"]:
            _walk(c)

    _walk(payload["layout"])
    assert titles["orch"] == "My Boss"
    assert titles["feed"] == "Activity"
```

- [ ] **Step 2: Run, expect failures (parameter not accepted, tool not registered)**

Run: `uv run pytest tests/test_orchestrator_tools_get_layout.py -v`
Expected: FAIL — `build_orchestrator_tools` does not accept `current_layout`, or `tools["get_layout"]` raises `KeyError`.

- [ ] **Step 3: Add the handler in tools.py**

Edit `patchfeld/orchestrator/tools.py`. Add a new handler builder near the other layout handlers (right after `_list_layouts_handler`):

```python
def _get_layout_handler(current_layout, widget_registry: WidgetRegistry):
    from patchfeld.layout.titles import populate_effective_titles

    async def get_layout_tool(_args: dict) -> dict:
        spec = current_layout() if current_layout is not None else None
        if spec is None:
            return {"content": [{"type": "text", "text": "No layout applied yet."}]}
        dumped = spec.model_dump(mode="json")
        try:
            populate_effective_titles(dumped["layout"], widget_registry)
        except Exception:
            pass  # Titles are advisory; never block the dump.
        return {"content": [{"type": "text", "text": json.dumps(dumped, indent=2)}]}

    return get_layout_tool
```

- [ ] **Step 4: Wire it into `build_orchestrator_tools`**

Edit `patchfeld/orchestrator/tools.py`. Change the `build_orchestrator_tools` signature to accept `current_layout`:

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
    current_layout=None,
):
```

Inside the function, after the existing `if widget_registry is not None:` block, add:

```python
    if widget_registry is not None and current_layout is not None:
        handlers["get_layout"] = _get_layout_handler(current_layout, widget_registry)
```

- [ ] **Step 5: Wire it into `build_orchestrator_mcp_server`**

Edit `patchfeld/orchestrator/tools.py`. Change the `build_orchestrator_mcp_server` signature to accept `current_layout`:

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
    current_layout=None,
):
```

Inside the function, after the existing `if widget_registry is not None:` block (which appends `list_widgets`), add:

```python
    if widget_registry is not None and current_layout is not None:
        sdk_tools.append(tool(
            "get_layout",
            "Returns the currently applied LayoutSpec as JSON. Each panel's "
            "`title` field is populated to its effective on-screen value, so "
            "you can match a user reference like 'the Activity Panel' against "
            "`title` to find the panel `id` you want to edit. Pass the "
            "modified spec back through `set_layout`.",
            {},
        )(_get_layout_handler(current_layout, widget_registry)))
```

- [ ] **Step 6: Forward `current_layout` through OrchestratorSession**

Edit `patchfeld/orchestrator/session.py`. Add `current_layout=None` to `__init__`'s signature:

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
        config_store=None,
        actions=None,
        rebind_keys=None,
        widget_registry=None,
        current_layout=None,
    ) -> None:
```

Store it: add `self._current_layout = current_layout` alongside the other `self._...` assignments.

In `start()`, pass it through to `build_orchestrator_mcp_server`:

```python
        mcp_server = build_orchestrator_mcp_server(
            self._manager,
            apply_layout=self._apply_layout,
            layouts_store=self._layouts_store,
            config_store=self._config_store,
            actions=self._actions,
            rebind_keys=self._rebind_keys,
            widget_registry=self._widget_registry,
            current_layout=self._current_layout,
        )
```

- [ ] **Step 7: Wire it from the App**

Edit `patchfeld/app.py`. Inside `PatchfeldApp.__init__`, change the `OrchestratorSession` construction to pass `current_layout`:

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
            current_layout=lambda: self._current_spec,
        )
```

- [ ] **Step 8: Run get_layout tests**

Run: `uv run pytest tests/test_orchestrator_tools_get_layout.py -v`
Expected: PASS, all three tests.

- [ ] **Step 9: Run the broader orchestrator + app suite**

Run: `uv run pytest tests/test_orchestrator_tools.py tests/test_orchestrator_tools_layout.py tests/test_orchestrator_tools_dict.py tests/test_orchestrator_tools_list_widgets.py tests/test_orchestrator_session.py tests/test_app_smoke_plan4.py tests/test_app_smoke_plan5.py tests/test_app_smoke_plan6.py -v`
Expected: PASS, no regressions.

- [ ] **Step 10: Commit**

```bash
git add patchfeld/orchestrator/tools.py patchfeld/orchestrator/session.py patchfeld/app.py \
        tests/test_orchestrator_tools_get_layout.py
git commit -m "feat(orchestrator): get_layout MCP tool with effective titles"
```

---

## Task 7: Update `set_layout` description and run the full suite

**Files:**
- Modify: `patchfeld/orchestrator/tools.py` (the `set_layout` description string in `build_orchestrator_mcp_server`)

- [ ] **Step 1: Update the `set_layout` advertised description**

Edit `patchfeld/orchestrator/tools.py`. Inside `build_orchestrator_mcp_server`, find the `set_layout` entry in `layout_specs` and replace its description string. The new description (full text):

```
"Replace the current UI layout with the given LayoutSpec dict. "
"Each panel may set an optional `title` field that overrides the widget's "
"default border title; titles are how the user refers to panels in chat "
"(e.g., 'make the Activity Panel 2x its size'). Call `get_layout` first "
"to discover effective titles before mutating. "
"If `spec.custom_widgets` is present, each entry's `source` "
"string is **exec'd in-process with full Python privileges** "
"to register a new Widget class before the layout is applied. "
"Only ship `custom_widgets` source you have personally "
"authored — anything you exec here can read files, hit the "
"network, and execute arbitrary code with the user's "
"permissions. The build-in widgets (list_widgets) are safer."
```

- [ ] **Step 2: Run the full test suite**

Run: `uv run pytest -x`
Expected: PASS, no failures.

- [ ] **Step 3: Manual smoke check (optional but recommended)**

Run the app: `uv run python -m patchfeld`
Expected:
  - Each panel in the dashboard shows a readable title in its top border ("Orchestrator", "Agents", "Activity").
  - Asking the orchestrator "what panels are visible?" results in it calling `get_layout` and reporting back with the titles.

If you can't run the app interactively, skip this step.

- [ ] **Step 4: Commit**

```bash
git add patchfeld/orchestrator/tools.py
git commit -m "docs(orchestrator): mention title field + get_layout in set_layout description"
```

---

## Self-Review Notes

**Spec coverage check:**
- `Panel.title` schema field — Task 1.
- Static `DEFAULT_BORDER_TITLE` on 4 widgets — Task 2.
- Prop-aware `default_border_title` classmethods on 8 widgets — Task 3.
- Five borderless widgets get `border:` rules — Task 4.
- Engine sets `border_title` and applies safety-net border — Task 5.
- `get_layout` MCP tool with effective titles — Task 6.
- `set_layout` description mentions `title` and recommends `get_layout` — Task 7.
- Resolver helper shared between engine and `get_layout` (no drift) — `patchfeld/layout/titles.py` introduced in Task 2 and reused in Task 5 (engine) and Task 6 (handler).
- Engine never aborts on a buggy `default_border_title` — Task 5 step 1 test #5.

**Type / signature consistency check:**
- `default_border_title(cls, props: dict) -> str` everywhere — verified across Tasks 3 and 5 tests.
- `resolve_title(panel, widget_cls)` accepts both `Panel` and dict — exercised by Task 2 (Panel) and Task 6 (dict via `populate_effective_titles`).
- `current_layout` keyword name consistent across `build_orchestrator_tools`, `build_orchestrator_mcp_server`, `OrchestratorSession.__init__`, and `PatchfeldApp` — verified in Task 6 steps 4–7.

**Pre-existing limitation (not addressed here, flagged in spec):**
- `apply()` rebuilds the whole container on any spec change, so a title-only edit will remount and lose transient widget state. Out of scope.
