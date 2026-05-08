# App-level and Panel-level Tabs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add persistent app-level tabs (each owns its own `LayoutSpec`) and a `Tabs` panel-level node, exposed via four new orchestrator MCP tools and three keybinding routes.

**Architecture:** A new `Workspace` model (list of `Tab` objects) replaces the singleton `LayoutSpec` at the app level; `PatchfeldApp` mounts a `TabbedContent` whose children are per-tab `Container#panel-area-<id>` slots. The existing `apply_layout` engine is reused per slot. A new `Tabs` node lives in the layout-spec union (leaf-only — each pane wraps one `Panel`). The current `LayoutSpec` "exactly one `OrchestratorChat`" invariant relaxes to "at most one"; `Workspace` enforces "at least one across all tabs."

**Tech Stack:** Textual ≥ 8 (`TabbedContent`, `TabPane`), Pydantic v2 (discriminated unions), pytest with `pilot`-style integration tests.

**Spec:** [`docs/superpowers/specs/2026-05-06-app-and-panel-tabs-design.md`](../specs/2026-05-06-app-and-panel-tabs-design.md)

---

## File map

**New files:**
- `patchfeld/workspace/__init__.py` — package marker
- `patchfeld/workspace/spec.py` — `Tab` and `Workspace` Pydantic models
- `patchfeld/persistence/workspace_store.py` — `load_workspace` / `save_workspace`
- `patchfeld/orchestrator/tabs_tools.py` — `add_tab`, `close_tab`, `switch_tab`, `list_tabs` handlers
- `patchfeld/widgets/new_tab_screen.py` — small modal asking for a tab title
- `tests/test_workspace_spec.py`
- `tests/test_workspace_store.py`
- `tests/test_layout_engine_tabs.py`
- `tests/test_orchestrator_tabs_tools.py`
- `tests/test_app_smoke_tabs.py`

**Modified files:**
- `patchfeld/layout/spec.py` — add `Tabs` node, switch to discriminated union, relax validator
- `patchfeld/layout/engine.py` — `_build` branch for `Tabs`, `_collect_panels` recurses into `Tabs`
- `patchfeld/events.py` — `TabAdded`/`TabClosed`/`TabSwitched`, optional `tab_id` on `LayoutApplied`/`LayoutFailed`
- `patchfeld/persistence/paths.py` — `project_workspace_path`
- `patchfeld/app.py` — compose `TabbedContent`, per-tab containers, migration, hotkeys
- `patchfeld/orchestrator/tools.py` — `set_layout`/`get_layout`/`save_layout`/`load_layout` accept `tab_id`; `load_layout` accepts `as_new_tab`; wire tab tools into `build_orchestrator_tools` and `build_orchestrator_mcp_server`
- `tests/test_layout_spec.py` — update for relaxed invariant + `Tabs`
- `tests/test_layout_engine_diff.py` — diff descends into Tabs

---

## Task 1: `Tabs` node + discriminated union in layout spec

**Files:**
- Modify: `patchfeld/layout/spec.py`
- Test: `tests/test_layout_spec.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_layout_spec.py`:

```python
from patchfeld.layout.spec import Tabs


def test_tabs_node_parses_with_panel_children():
    spec = LayoutSpec.model_validate({
        "version": 1,
        "layout": {
            "type": "horizontal",
            "children": [
                {"id": "orch", "widget": "OrchestratorChat"},
                {
                    "type": "tabs",
                    "children": [
                        {"id": "feed", "widget": "ActivityFeed", "title": "Activity"},
                        {"id": "logs", "widget": "LogTail", "title": "SQL logs"},
                    ],
                    "active": "logs",
                },
            ],
        },
    })
    root = spec.layout
    assert isinstance(root, Container)
    tabs_node = root.children[1]
    assert isinstance(tabs_node, Tabs)
    assert [p.id for p in tabs_node.children] == ["feed", "logs"]
    assert tabs_node.active == "logs"


def test_tabs_node_rejects_empty_children():
    with pytest.raises(ValueError):
        LayoutSpec.model_validate({
            "version": 1,
            "layout": {
                "type": "horizontal",
                "children": [
                    {"id": "orch", "widget": "OrchestratorChat"},
                    {"type": "tabs", "children": []},
                ],
            },
        })


def test_tabs_node_rejects_container_child():
    # Tabs is leaf-only: each tab must be a Panel, never a Container.
    with pytest.raises(ValueError):
        LayoutSpec.model_validate({
            "version": 1,
            "layout": {
                "type": "horizontal",
                "children": [
                    {"id": "orch", "widget": "OrchestratorChat"},
                    {
                        "type": "tabs",
                        "children": [
                            {"type": "horizontal", "children": [
                                {"id": "x", "widget": "AgentTable"},
                            ]},
                        ],
                    },
                ],
            },
        })


def test_tabs_node_active_defaults_to_none():
    spec = LayoutSpec.model_validate({
        "version": 1,
        "layout": {
            "type": "horizontal",
            "children": [
                {"id": "orch", "widget": "OrchestratorChat"},
                {
                    "type": "tabs",
                    "children": [{"id": "a", "widget": "AgentTable"}],
                },
            ],
        },
    })
    tabs_node = spec.layout.children[1]
    assert isinstance(tabs_node, Tabs)
    assert tabs_node.active is None


def test_container_with_horizontal_type_still_parses():
    # Sanity: discriminated-union refactor preserves existing behavior.
    spec = LayoutSpec.model_validate({
        "version": 1,
        "layout": {
            "type": "horizontal",
            "children": [{"id": "orch", "widget": "OrchestratorChat"}],
        },
    })
    assert isinstance(spec.layout, Container)
    assert spec.layout.type == "horizontal"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_layout_spec.py -v -k "tabs_node or container_with_horizontal_type"`
Expected: FAIL — `cannot import name 'Tabs'` and the new tests do not exist.

- [ ] **Step 3: Replace the union definition in `patchfeld/layout/spec.py`**

Replace the existing file contents with:

```python
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Panel(BaseModel):
    """A leaf node — one widget instance."""
    model_config = ConfigDict(extra="forbid")

    id: str
    widget: str
    props: dict = Field(default_factory=dict)
    size: str | None = None
    title: str | None = None


class Container(BaseModel):
    """A non-leaf node — splits its area horizontally or vertically."""
    model_config = ConfigDict(extra="forbid")

    type: Literal["horizontal", "vertical"]
    size: str | None = None
    children: list["Node"] = Field(min_length=1)


class Tabs(BaseModel):
    """A tabbed leaf-container — each child is one widget reachable via a
    per-panel tab strip. Each tab holds exactly one Panel; splits inside
    a single tab are not allowed.

    `active` is the panel id of the initial tab; when None, the first
    child is the initial tab."""
    model_config = ConfigDict(extra="forbid")

    type: Literal["tabs"]
    size: str | None = None
    children: list[Panel] = Field(min_length=1)
    active: str | None = None


# Discriminated union: Container and Tabs share the `children` shape but
# differ on `type`. Pydantic v2 dispatches on the literal `type` value.
# Panel has no `type` field and is matched by absence — placed last so
# union resolution prefers the typed branches first.
_TypedNode = Annotated[Union[Container, Tabs], Field(discriminator="type")]
Node = Union[_TypedNode, Panel]
Container.model_rebuild()


class CustomWidget(BaseModel):
    """A user/orchestrator-supplied Textual widget class."""
    model_config = ConfigDict(extra="forbid")

    name: str
    source: str


class LayoutSpec(BaseModel):
    """Root of the layout description.

    Validation invariant: at most one panel with widget='OrchestratorChat'
    in `layout`. The "at least one chat across all tabs" half is enforced
    by the Workspace model — a single LayoutSpec may have zero chats
    (e.g., a logs-only tab).

    The `focus` field names a panel id to receive keyboard focus on apply,
    but is NOT validated against the tree at parse time — LayoutEngine.apply
    silently no-ops if the id does not exist when the layout is mounted.
    """
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    layout: Node
    focus: str | None = None
    custom_widgets: list[CustomWidget] = Field(default_factory=list)

    @model_validator(mode="after")
    def _at_most_one_orchestrator_chat(self) -> "LayoutSpec":
        count = _count_orchestrator(self.layout)
        if count > 1:
            raise ValueError(
                "LayoutSpec must contain at most one OrchestratorChat panel"
            )
        return self


def _count_orchestrator(node: Node) -> int:
    if isinstance(node, Panel):
        return 1 if node.widget == "OrchestratorChat" else 0
    if isinstance(node, Tabs):
        return sum(1 for c in node.children if c.widget == "OrchestratorChat")
    # Container
    return sum(_count_orchestrator(c) for c in node.children)
```

- [ ] **Step 4: Update existing `LayoutSpec` test for the relaxed invariant**

The existing `test_spec_without_orchestrator_chat_is_rejected` test expects a chat-less spec to be rejected; under the new invariant that's allowed. Replace it in `tests/test_layout_spec.py`:

```python
def test_spec_without_orchestrator_chat_is_now_accepted():
    # Per design: LayoutSpec allows zero OrchestratorChat. Workspace
    # enforces "at least one across all tabs."
    spec = LayoutSpec.model_validate({
        "version": 1,
        "layout": {"id": "x", "widget": "AgentTable"},
    })
    assert isinstance(spec.layout, Panel)
    assert spec.layout.widget == "AgentTable"


def test_spec_with_two_orchestrator_chats_is_rejected():
    with pytest.raises(ValueError, match="at most one"):
        LayoutSpec.model_validate({
            "version": 1,
            "layout": {
                "type": "horizontal",
                "children": [
                    {"id": "a", "widget": "OrchestratorChat"},
                    {"id": "b", "widget": "OrchestratorChat"},
                ],
            },
        })
```

(Replace the original `test_spec_without_orchestrator_chat_is_rejected` and `test_spec_with_two_orchestrator_chats_is_rejected` with the two functions above. The original two-chat test asserted on `match="exactly one"` — update to `"at most one"`.)

- [ ] **Step 5: Run all spec tests**

Run: `uv run pytest tests/test_layout_spec.py -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add patchfeld/layout/spec.py tests/test_layout_spec.py
git commit -m "feat(layout): add Tabs node + relax OrchestratorChat invariant to at-most-one"
```

---

## Task 2: `Workspace` and `Tab` Pydantic models

**Files:**
- Create: `patchfeld/workspace/__init__.py`
- Create: `patchfeld/workspace/spec.py`
- Test: `tests/test_workspace_spec.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_workspace_spec.py`:

```python
import pytest

from patchfeld.layout.spec import LayoutSpec
from patchfeld.workspace.spec import Tab, Workspace


def _layout_with_chat(panel_id: str = "orch") -> dict:
    return {
        "version": 1,
        "layout": {"id": panel_id, "widget": "OrchestratorChat"},
    }


def _layout_chatless() -> dict:
    return {
        "version": 1,
        "layout": {"id": "feed", "widget": "ActivityFeed"},
    }


def test_workspace_with_one_chat_tab_parses():
    ws = Workspace.model_validate({
        "version": 1,
        "tabs": [
            {"id": "t1", "title": "Main", "layout": _layout_with_chat()},
        ],
        "active": "t1",
    })
    assert ws.active == "t1"
    assert len(ws.tabs) == 1
    assert isinstance(ws.tabs[0].layout, LayoutSpec)


def test_workspace_with_chatless_tab_only_is_rejected():
    with pytest.raises(ValueError, match="at least one OrchestratorChat"):
        Workspace.model_validate({
            "version": 1,
            "tabs": [
                {"id": "t1", "title": "Logs", "layout": _layout_chatless()},
            ],
            "active": "t1",
        })


def test_workspace_with_chat_in_one_of_many_tabs_is_accepted():
    ws = Workspace.model_validate({
        "version": 1,
        "tabs": [
            {"id": "t1", "title": "Logs", "layout": _layout_chatless()},
            {"id": "t2", "title": "Main", "layout": _layout_with_chat()},
        ],
        "active": "t2",
    })
    assert {t.id for t in ws.tabs} == {"t1", "t2"}


def test_workspace_active_must_reference_existing_tab():
    with pytest.raises(ValueError, match="active tab id"):
        Workspace.model_validate({
            "version": 1,
            "tabs": [
                {"id": "t1", "title": "Main", "layout": _layout_with_chat()},
            ],
            "active": "ghost",
        })


def test_workspace_rejects_empty_tabs():
    with pytest.raises(ValueError):
        Workspace.model_validate({"version": 1, "tabs": [], "active": "x"})


def test_workspace_rejects_duplicate_tab_ids():
    with pytest.raises(ValueError, match="duplicate tab id"):
        Workspace.model_validate({
            "version": 1,
            "tabs": [
                {"id": "t1", "title": "A", "layout": _layout_with_chat("a")},
                {"id": "t1", "title": "B", "layout": _layout_chatless()},
            ],
            "active": "t1",
        })


def test_workspace_round_trips_through_json():
    ws = Workspace.model_validate({
        "version": 1,
        "tabs": [
            {"id": "t1", "title": "Main", "layout": _layout_with_chat()},
        ],
        "active": "t1",
    })
    again = Workspace.model_validate_json(ws.model_dump_json())
    assert again == ws


def test_workspace_chat_in_panel_tabs_node_counts():
    # OrchestratorChat hidden inside a panel-level Tabs node still satisfies
    # the workspace invariant (we walk Tabs.children too).
    ws = Workspace.model_validate({
        "version": 1,
        "tabs": [{
            "id": "t1",
            "title": "Main",
            "layout": {
                "version": 1,
                "layout": {
                    "type": "tabs",
                    "children": [
                        {"id": "orch", "widget": "OrchestratorChat"},
                        {"id": "feed", "widget": "ActivityFeed"},
                    ],
                },
            },
        }],
        "active": "t1",
    })
    assert ws.tabs[0].layout.layout.children[0].widget == "OrchestratorChat"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_workspace_spec.py -v`
Expected: FAIL — module `patchfeld.workspace` doesn't exist.

- [ ] **Step 3: Create the package marker**

Create `patchfeld/workspace/__init__.py`:

```python
```

(Empty file — package marker only.)

- [ ] **Step 4: Implement `Workspace` and `Tab`**

Create `patchfeld/workspace/spec.py`:

```python
from pydantic import BaseModel, ConfigDict, Field, model_validator

from patchfeld.layout.spec import Container, LayoutSpec, Node, Panel, Tabs


class Tab(BaseModel):
    """One app-level tab. Owns its own LayoutSpec, which is independently
    mutable. `id` is stable across the tab's lifetime and used by
    switch_tab/close_tab tool calls. `title` is the user-facing tab-strip
    label."""
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    layout: LayoutSpec


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

    @model_validator(mode="after")
    def _validate(self) -> "Workspace":
        ids = [t.id for t in self.tabs]
        if len(ids) != len(set(ids)):
            raise ValueError(f"duplicate tab id in {ids}")
        if self.active not in set(ids):
            raise ValueError(
                f"active tab id {self.active!r} not in tab ids {ids}"
            )
        if not any(_contains_chat(t.layout.layout) for t in self.tabs):
            raise ValueError(
                "Workspace must contain at least one OrchestratorChat panel "
                "across all tabs"
            )
        return self


def _contains_chat(node: Node) -> bool:
    if isinstance(node, Panel):
        return node.widget == "OrchestratorChat"
    if isinstance(node, Tabs):
        return any(c.widget == "OrchestratorChat" for c in node.children)
    if isinstance(node, Container):
        return any(_contains_chat(c) for c in node.children)
    return False
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_workspace_spec.py -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add patchfeld/workspace/__init__.py patchfeld/workspace/spec.py tests/test_workspace_spec.py
git commit -m "feat(workspace): Tab and Workspace pydantic models with invariants"
```

---

## Task 3: Workspace persistence store

**Files:**
- Modify: `patchfeld/persistence/paths.py`
- Create: `patchfeld/persistence/workspace_store.py`
- Test: `tests/test_workspace_store.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_workspace_store.py`:

```python
import json
from pathlib import Path

from patchfeld.persistence.workspace_store import (
    load_workspace,
    save_workspace,
)
from patchfeld.workspace.spec import Tab, Workspace


def _ws(tmp_path: Path) -> Workspace:
    return Workspace.model_validate({
        "version": 1,
        "tabs": [
            {
                "id": "t1",
                "title": "Main",
                "layout": {
                    "version": 1,
                    "layout": {"id": "orch", "widget": "OrchestratorChat"},
                },
            },
        ],
        "active": "t1",
    })


def test_save_then_load_round_trips(tmp_path):
    src = _ws(tmp_path)
    save_workspace(tmp_path, src)
    again = load_workspace(tmp_path)
    assert again == src


def test_load_returns_none_when_no_file(tmp_path):
    assert load_workspace(tmp_path) is None


def test_load_returns_none_for_corrupt_file(tmp_path):
    target = tmp_path / ".patchfeld" / "workspace.json"
    target.parent.mkdir(parents=True)
    target.write_text("{not json")
    assert load_workspace(tmp_path) is None


def test_save_writes_to_workspace_json(tmp_path):
    save_workspace(tmp_path, _ws(tmp_path))
    target = tmp_path / ".patchfeld" / "workspace.json"
    assert target.exists()
    raw = json.loads(target.read_text())
    assert raw["active"] == "t1"
    assert [t["id"] for t in raw["tabs"]] == ["t1"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_workspace_store.py -v`
Expected: FAIL — `cannot import name 'load_workspace'`.

- [ ] **Step 3: Add the workspace path helper**

Edit `patchfeld/persistence/paths.py` — add at the end of the file (after `project_layout_path`):

```python
def project_workspace_path(cwd: Path) -> Path:
    return project_state_dir(cwd) / "workspace.json"
```

- [ ] **Step 4: Implement the store**

Create `patchfeld/persistence/workspace_store.py`:

```python
import json
import logging
from pathlib import Path

from patchfeld.persistence.atomic import write_json_atomic
from patchfeld.persistence.paths import project_workspace_path
from patchfeld.workspace.spec import Workspace

log = logging.getLogger(__name__)


def save_workspace(cwd: Path, ws: Workspace) -> None:
    write_json_atomic(project_workspace_path(cwd), ws.model_dump(mode="json"))


def load_workspace(cwd: Path) -> Workspace | None:
    path = project_workspace_path(cwd)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return Workspace.model_validate(raw)
    except Exception:
        log.exception("Failed to load workspace from %s", path)
        return None
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_workspace_store.py tests/test_paths.py -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add patchfeld/persistence/paths.py patchfeld/persistence/workspace_store.py tests/test_workspace_store.py
git commit -m "feat(persistence): workspace_store with atomic save/load"
```

---

## Task 4: Layout engine — `Tabs` build branch

**Files:**
- Modify: `patchfeld/layout/engine.py`
- Test: `tests/test_layout_engine_tabs.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_layout_engine_tabs.py`:

```python
import pytest
from textual.app import App, ComposeResult
from textual.containers import Container as TxContainer
from textual.widgets import TabbedContent, TabPane

from patchfeld.layout.engine import apply
from patchfeld.layout.registry import WidgetRegistry
from patchfeld.layout.spec import LayoutSpec
from patchfeld.widgets.orchestrator_chat import OrchestratorChat
from patchfeld.widgets.placeholders import ActivityFeed
from patchfeld.widgets.log_tail import LogTail


def _registry() -> WidgetRegistry:
    reg = WidgetRegistry()
    reg.register("OrchestratorChat", OrchestratorChat)
    reg.register("ActivityFeed", ActivityFeed)
    reg.register("LogTail", LogTail)
    return reg


class _Host(App):
    """Minimal App that holds a single panel-area Container as the apply target."""
    def compose(self) -> ComposeResult:
        yield TxContainer(id="panel-area")


def _spec_with_tabs() -> LayoutSpec:
    return LayoutSpec.model_validate({
        "version": 1,
        "layout": {
            "type": "horizontal",
            "children": [
                {"id": "orch", "widget": "OrchestratorChat"},
                {
                    "type": "tabs",
                    "children": [
                        {"id": "feed", "widget": "ActivityFeed", "title": "Activity"},
                        {"id": "logs", "widget": "LogTail", "title": "Logs"},
                    ],
                    "active": "logs",
                },
            ],
        },
    })


@pytest.mark.asyncio
async def test_tabs_node_builds_into_tabbedcontent():
    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        area = app.query_one("#panel-area", TxContainer)
        await apply(area, _spec_with_tabs(), _registry())
        await pilot.pause()
        tcs = app.query(TabbedContent)
        assert len(tcs) == 1
        panes = tcs.first().query(TabPane)
        assert {p.id for p in panes} == {"tabpane-feed", "tabpane-logs"}


@pytest.mark.asyncio
async def test_tabs_active_field_selects_initial_pane():
    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        area = app.query_one("#panel-area", TxContainer)
        await apply(area, _spec_with_tabs(), _registry())
        await pilot.pause()
        tc = app.query_one(TabbedContent)
        # active="logs" -> initial tab pane id should be tabpane-logs
        assert tc.active == "tabpane-logs"


@pytest.mark.asyncio
async def test_tabs_panels_are_mounted_with_panel_id_format():
    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        area = app.query_one("#panel-area", TxContainer)
        await apply(area, _spec_with_tabs(), _registry())
        await pilot.pause()
        # Each child Panel inside Tabs gets the same `panel-<id>` id treatment
        # as a regular Panel, so set_layout / focus_panel still work uniformly.
        assert app.query_one("#panel-feed") is not None
        assert app.query_one("#panel-logs") is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_layout_engine_tabs.py -v`
Expected: FAIL — `_build` doesn't handle the `Tabs` branch (panes not produced).

- [ ] **Step 3: Add the `Tabs` branch to `_build`**

Edit `patchfeld/layout/engine.py`. Add a new import near the top (with the other Textual imports):

```python
from textual.widgets import TabbedContent, TabPane
```

Add `Tabs` to the import from `patchfeld.layout.spec`:

```python
from patchfeld.layout.spec import Container, LayoutSpec, Panel, Tabs
```

Replace the body of `_build` so it dispatches on all three node types:

```python
def _build(node, registry) -> "TxContainer":
    if isinstance(node, Panel):
        cls = registry.get(node.widget)
        widget = cls(**node.props) if node.props else cls()
        widget.id = f"panel-{node.id}"
        widget.can_focus = True  # panels must be focusable so focus survives rebuilds
        if node.size:
            widget.styles.width = node.size if "%" in node.size or node.size.endswith("fr") else None
            widget.styles.height = None
        if not _has_border_in_default_css(cls):
            widget.styles.border = ("round", "#3a3a3a")
        try:
            widget.border_title = resolve_title(node, cls)
        except Exception:
            widget.border_title = cls.__name__
        return widget
    if isinstance(node, Tabs):
        panes = []
        for child in node.children:
            inner = _build(child, registry)            # reuses Panel branch
            label = child.title or _default_pane_label(child, registry)
            panes.append(TabPane(label, inner, id=f"tabpane-{child.id}"))
        initial_id = f"tabpane-{node.active or node.children[0].id}"
        tc = TabbedContent(*panes, initial=initial_id)
        if node.size:
            tc.styles.width = node.size
        return tc
    # Container
    box_cls = Horizontal if node.type == "horizontal" else Vertical
    box = box_cls(*[_build(c, registry) for c in node.children])
    if node.size:
        box.styles.width = node.size
    return box


def _default_pane_label(panel: Panel, registry) -> str:
    """Best-effort label for a TabPane when the panel has no explicit title.
    Reuses resolve_title against the widget class so widgets that publish a
    DEFAULT_BORDER_TITLE feed the tab label too."""
    try:
        cls = registry.get(panel.widget)
        return resolve_title(panel, cls)
    except Exception:
        return panel.widget
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_layout_engine_tabs.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add patchfeld/layout/engine.py tests/test_layout_engine_tabs.py
git commit -m "feat(engine): build Tabs node into TabbedContent with TabPanes"
```

---

## Task 5: Layout engine — `diff` descends into `Tabs`

**Files:**
- Modify: `patchfeld/layout/engine.py`
- Test: `tests/test_layout_engine_diff.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_layout_engine_diff.py`:

```python
def _spec_with_tabs(tabs_children: list[dict]) -> LayoutSpec:
    return LayoutSpec.model_validate({
        "version": 1,
        "layout": {
            "type": "horizontal",
            "children": [
                {"id": "orch", "widget": "OrchestratorChat"},
                {"type": "tabs", "children": tabs_children},
            ],
        },
    })


def test_panel_inside_tabs_props_change_produces_update():
    a = _spec_with_tabs([
        {"id": "feed", "widget": "ActivityFeed", "props": {"x": 1}},
        {"id": "logs", "widget": "LogTail"},
    ])
    b = _spec_with_tabs([
        {"id": "feed", "widget": "ActivityFeed", "props": {"x": 2}},
        {"id": "logs", "widget": "LogTail"},
    ])
    ops = diff(a, b)
    assert ops == [UpdateProps(panel_id="feed", props={"x": 2})]


def test_panel_added_to_tabs_is_mounted():
    a = _spec_with_tabs([
        {"id": "feed", "widget": "ActivityFeed"},
    ])
    b = _spec_with_tabs([
        {"id": "feed", "widget": "ActivityFeed"},
        {"id": "logs", "widget": "LogTail"},
    ])
    ops = diff(a, b)
    assert any(isinstance(op, MountPanel) and op.panel.id == "logs" for op in ops)


def test_panel_removed_from_tabs_is_unmounted():
    a = _spec_with_tabs([
        {"id": "feed", "widget": "ActivityFeed"},
        {"id": "logs", "widget": "LogTail"},
    ])
    b = _spec_with_tabs([
        {"id": "feed", "widget": "ActivityFeed"},
    ])
    ops = diff(a, b)
    assert any(isinstance(op, UnmountPanel) and op.panel_id == "logs" for op in ops)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_layout_engine_diff.py -v -k "tabs"`
Expected: FAIL — `_collect_panels` doesn't descend into `Tabs` children.

- [ ] **Step 3: Update `_collect_panels` in `patchfeld/layout/engine.py`**

Replace the existing function:

```python
def _collect_panels(node, out: dict[str, Panel]) -> None:
    if isinstance(node, Panel):
        out[node.id] = node
    elif isinstance(node, Tabs):
        for c in node.children:
            out[c.id] = c
    elif isinstance(node, Container):
        for c in node.children:
            _collect_panels(c, out)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_layout_engine_diff.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add patchfeld/layout/engine.py tests/test_layout_engine_diff.py
git commit -m "feat(engine): diff walks into Tabs.children for mount/unmount/update"
```

---

## Task 6: Tab events + `tab_id` on `LayoutApplied`/`LayoutFailed`

**Files:**
- Modify: `patchfeld/events.py`
- Test: `tests/test_events.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_events.py` (create it if it doesn't exist; check first with the snippet below):

```python
from patchfeld.events import (
    LayoutApplied,
    LayoutFailed,
    TabAdded,
    TabClosed,
    TabSwitched,
)


def test_tab_added_event_has_id_and_title():
    e = TabAdded(tab_id="t1", title="Main")
    assert e.tab_id == "t1"
    assert e.title == "Main"


def test_tab_closed_event_has_id():
    e = TabClosed(tab_id="t1")
    assert e.tab_id == "t1"


def test_tab_switched_event_has_id_and_title():
    e = TabSwitched(tab_id="t1", title="Main")
    assert (e.tab_id, e.title) == ("t1", "Main")


def test_layout_applied_includes_tab_id():
    from patchfeld.layout.spec import LayoutSpec
    spec = LayoutSpec.model_validate({
        "version": 1,
        "layout": {"id": "orch", "widget": "OrchestratorChat"},
    })
    e = LayoutApplied(spec=spec, layout_name=None, tab_id="t1")
    assert e.tab_id == "t1"


def test_layout_applied_tab_id_defaults_to_none():
    from patchfeld.layout.spec import LayoutSpec
    spec = LayoutSpec.model_validate({
        "version": 1,
        "layout": {"id": "orch", "widget": "OrchestratorChat"},
    })
    e = LayoutApplied(spec=spec)
    assert e.tab_id is None


def test_layout_failed_includes_tab_id():
    e = LayoutFailed(error="boom", tab_id="t1")
    assert e.tab_id == "t1"
    e2 = LayoutFailed(error="boom")
    assert e2.tab_id is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_events.py -v -k "tab"`
Expected: FAIL — `cannot import name 'TabAdded'` etc.

- [ ] **Step 3: Add the events**

In `patchfeld/events.py`, modify `LayoutApplied` and `LayoutFailed` to add the optional `tab_id`, and append the three new dataclasses after `LayoutFailed`:

```python
@dataclass(frozen=True)
class LayoutApplied:
    """The LayoutEngine successfully applied a new spec."""
    spec: "LayoutSpec"
    layout_name: str | None = None
    tab_id: str | None = None  # set when published per-tab


@dataclass(frozen=True)
class LayoutFailed:
    """The LayoutEngine rejected a spec at build time."""
    error: str
    tab_id: str | None = None


@dataclass(frozen=True)
class TabAdded:
    tab_id: str
    title: str


@dataclass(frozen=True)
class TabClosed:
    tab_id: str


@dataclass(frozen=True)
class TabSwitched:
    tab_id: str
    title: str
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_events.py -v`
Expected: all pass.

- [ ] **Step 5: Verify no existing publishers broke**

The `tab_id` field is optional with a `None` default — existing `LayoutApplied(spec=..., layout_name=...)` constructions still work.

Run: `uv run pytest tests/ -v -k "layout or events"`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add patchfeld/events.py tests/test_events.py
git commit -m "feat(events): TabAdded/TabClosed/TabSwitched + tab_id on LayoutApplied/Failed"
```

---

## Task 7: `PatchfeldApp` — compose with `TabbedContent` and per-tab containers

**Files:**
- Modify: `patchfeld/app.py`
- Test: `tests/test_app_smoke_tabs.py` (new)

This is the largest single task: it threads the workspace concept through the App. We do it as one task because the changes are tightly coupled — partial states won't run.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_app_smoke_tabs.py`:

```python
import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock
from textual.containers import Container as TxContainer
from textual.widgets import TabbedContent, TabPane

from patchfeld.agents.fake_sdk_adapter import FakeSDKAdapter
from patchfeld.agents.manager import AgentManager
from patchfeld.app import PatchfeldApp
from patchfeld.events import EventBus
from patchfeld.orchestrator.session import OrchestratorSession


def _ok():
    return [
        AssistantMessage(content=[TextBlock(text="ok")], model="fake-model"),
        ResultMessage(
            subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
            num_turns=1, session_id="fake", total_cost_usd=0.0,
            usage={"input_tokens": 1, "output_tokens": 1}, result="ok",
        ),
    ]


def _build_app(tmp_path):
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
    )
    return app


@pytest.mark.asyncio
async def test_app_starts_with_one_tab_when_no_workspace_exists(tmp_path):
    app = _build_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        tc = app.query_one("#app-tabs", TabbedContent)
        panes = tc.query(TabPane)
        assert len(panes) == 1
        # The single tab's panel-area container is mounted with a per-tab id.
        only_pane = panes.first()
        # the container inside the pane has id panel-area-<tab.id>
        area = only_pane.query_one("[id^=panel-area-]", TxContainer)
        assert area.id is not None and area.id.startswith("panel-area-")


@pytest.mark.asyncio
async def test_app_seeds_dashboard_layout_on_first_run(tmp_path):
    app = _build_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        # The first tab's layout should be the built-in dashboard.
        # Assert the orchestrator chat panel is present somewhere in the DOM.
        assert app.query_one("#panel-orch") is not None


@pytest.mark.asyncio
async def test_app_writes_workspace_json_on_launch(tmp_path):
    app = _build_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        ws_path = tmp_path / ".patchfeld" / "workspace.json"
        assert ws_path.exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_app_smoke_tabs.py -v`
Expected: FAIL — `#app-tabs` doesn't exist.

- [ ] **Step 3: Add a workspace-bootstrap helper**

Append to `patchfeld/workspace/spec.py`:

```python
def workspace_from_layout(spec: LayoutSpec, *, tab_id: str = "default",
                          title: str = "default") -> Workspace:
    """Build a single-tab Workspace wrapping a LayoutSpec — used by app
    launch to seed the workspace from the built-in dashboard or migrate
    a legacy layout.json."""
    return Workspace(
        version=1,
        tabs=[Tab(id=tab_id, title=title, layout=spec)],
        active=tab_id,
    )
```

- [ ] **Step 4: Refactor `PatchfeldApp` to host a `TabbedContent`**

Edit `patchfeld/app.py`:

1. Add imports near the top:

```python
from textual.widgets import TabbedContent, TabPane

from patchfeld.persistence.workspace_store import (
    load_workspace as load_local_workspace,
    save_workspace as save_local_workspace,
)
from patchfeld.workspace.spec import Tab, Workspace, workspace_from_layout
```

2. Replace the legacy layout-store imports:

```python
# REMOVE:
# from patchfeld.persistence.layout_store import load_layout as load_local_layout
# from patchfeld.persistence.layout_store import save_layout as save_local_layout
```

   …leaving only the workspace store imports.

3. Replace the per-app `_current_spec` / `_current_layout_name` instance state with workspace state. In `__init__`:

```python
self._workspace: Workspace | None = None
self._active_tab_id: str | None = None
self._current_layout_name: str | None = None  # last `load_layout` name
self._tab_focus_snapshots: dict[str, str] = {}  # tab_id -> last focused panel id
```

   Remove the `self._current_spec: LayoutSpec | None = None` line.

4. Update `current_layout=...` in the `OrchestratorSession` construction to pull from the active tab:

```python
self.orchestrator = orchestrator or OrchestratorSession(
    ...,
    current_layout=lambda: self._active_layout(),
)
```

   And add a helper method:

```python
def _active_layout(self) -> LayoutSpec | None:
    if self._workspace is None or self._active_tab_id is None:
        return None
    for t in self._workspace.tabs:
        if t.id == self._active_tab_id:
            return t.layout
    return None
```

5. Replace `compose`:

```python
def compose(self) -> ComposeResult:
    yield CommandBar(event_bus=self.event_bus)
    yield TabbedContent(id="app-tabs")
    yield StatusBar(event_bus=self.event_bus)
```

6. Replace `on_mount`:

```python
async def on_mount(self) -> None:
    self._rebind_keys()
    if self.layouts_store.load("default") is None:
        self.layouts_store.save("default", dashboard_layout())
    await self.orchestrator.start()
    self._workspace = self._load_or_seed_workspace()
    self._active_tab_id = self._workspace.active
    await self._mount_workspace(self._workspace)
    save_local_workspace(self.cwd, self._workspace)
```

7. Add the load/seed/migrate helper:

```python
def _load_or_seed_workspace(self) -> Workspace:
    """Load workspace.json, fall back to migrating layout.json, fall back
    to seeding from the built-in dashboard."""
    ws = load_local_workspace(self.cwd)
    if ws is not None:
        return ws
    # Migration: legacy layout.json -> single-tab workspace.
    from patchfeld.persistence.layout_store import load_layout as _load_legacy
    legacy = _load_legacy(self.cwd)
    if legacy is not None:
        return workspace_from_layout(legacy, tab_id="default", title="default")
    return workspace_from_layout(dashboard_layout(), tab_id="default", title="default")
```

8. Add the workspace-mount helper. This is the up-front mount that honors "persistent — keep mounted":

```python
async def _mount_workspace(self, ws: Workspace) -> None:
    tc = self.query_one("#app-tabs", TabbedContent)
    # Build one TabPane per Tab, each containing a panel-area-<id> Container.
    new_panes = []
    for t in ws.tabs:
        new_panes.append(
            TabPane(t.title, TxContainer(id=f"panel-area-{t.id}"), id=f"tab-{t.id}")
        )
    await tc.clear_panes()
    for pane in new_panes:
        await tc.add_pane(pane)
    # Apply each tab's layout to its container, eagerly (persistent semantics).
    for t in ws.tabs:
        area = self.query_one(f"#panel-area-{t.id}", TxContainer)
        try:
            await apply_layout(area, t.layout, self.registry, layout_name=None)
        except Exception:
            # Apply errors already publish LayoutFailed; swallow here so one
            # bad tab doesn't block the rest of the workspace from booting.
            pass
    tc.active = f"tab-{ws.active}"
```

9. Replace `_apply` so it operates on the active tab:

```python
async def _orchestrator_apply_layout(
    self, spec: LayoutSpec, *, layout_name: str | None = None,
) -> None:
    await self._apply_to_tab(self._active_tab_id, spec, layout_name=layout_name)


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
    area = self.query_one(f"#panel-area-{tab_id}", TxContainer)
    await apply_layout(area, spec, self.registry, layout_name=layout_name)
    self._current_layout_name = layout_name
    save_local_workspace(self.cwd, self._workspace)
```

10. Remove the legacy `save_local_layout(self.cwd, spec)` call from `_apply` (and the `_apply` method itself if no longer used) — its job is done by `save_local_workspace` above.

11. Update `action_open_layout_switcher`'s callback so it routes through `_apply_to_tab` (the existing call already goes through `_orchestrator_apply_layout`, which now routes to the active tab). No change needed there.

- [ ] **Step 5: Run smoke tests to verify they pass**

Run: `uv run pytest tests/test_app_smoke_tabs.py -v`
Expected: all 3 tests pass.

- [ ] **Step 6: Run the full existing app-smoke suite to catch regressions**

Run: `uv run pytest tests/test_app_smoke.py tests/test_app_smoke_plan2.py tests/test_app_smoke_plan3.py tests/test_app_smoke_plan4.py tests/test_app_smoke_plan5.py tests/test_app_smoke_plan6.py -v`
Expected: all pass. If any test asserts on `#panel-area` directly (the old singleton container id), update it to `#panel-area-default` or `[id^=panel-area-]`. Fix call-sites that still touch `app._current_spec` to read from `app._active_layout()`.

- [ ] **Step 7: Commit**

```bash
git add patchfeld/app.py patchfeld/workspace/spec.py tests/test_app_smoke_tabs.py tests/test_app_smoke*.py
git commit -m "feat(app): mount Workspace as TabbedContent with per-tab panel-area containers"
```

---

## Task 8: Migration test — legacy `layout.json` becomes a one-tab workspace

**Files:**
- Test: `tests/test_app_smoke_tabs.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_app_smoke_tabs.py`:

```python
import json


@pytest.mark.asyncio
async def test_legacy_layout_json_is_migrated_to_workspace(tmp_path):
    # Seed a legacy layout.json on disk before the app starts.
    legacy = {
        "version": 1,
        "layout": {
            "type": "horizontal",
            "children": [
                {"id": "orch", "widget": "OrchestratorChat", "size": "70%"},
                {"id": "feed", "widget": "ActivityFeed", "size": "30%"},
            ],
        },
        "focus": "orch",
    }
    (tmp_path / ".patchfeld").mkdir()
    (tmp_path / ".patchfeld" / "layout.json").write_text(json.dumps(legacy))

    app = _build_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        # workspace.json now exists, single tab, contains the legacy layout.
        ws_raw = json.loads((tmp_path / ".patchfeld" / "workspace.json").read_text())
        assert len(ws_raw["tabs"]) == 1
        assert ws_raw["active"] == "default"
        assert ws_raw["tabs"][0]["layout"]["focus"] == "orch"
        # And the legacy file is left in place for a release as a safety net.
        assert (tmp_path / ".patchfeld" / "layout.json").exists()
```

- [ ] **Step 2: Run test to verify it passes**

Run: `uv run pytest tests/test_app_smoke_tabs.py::test_legacy_layout_json_is_migrated_to_workspace -v`
Expected: PASS (the migration code already lives in `_load_or_seed_workspace` from Task 7).

- [ ] **Step 3: Commit**

```bash
git add tests/test_app_smoke_tabs.py
git commit -m "test(app): legacy layout.json migrates to single-tab workspace"
```

---

## Task 9: `TabActivated` handler — saves workspace, fires event, restores focus

**Files:**
- Modify: `patchfeld/app.py`
- Test: `tests/test_app_smoke_tabs.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_app_smoke_tabs.py`:

```python
@pytest.mark.asyncio
async def test_tab_activation_updates_workspace_active(tmp_path):
    # Seed a 2-tab workspace, then activate the second tab via TabbedContent
    # API and assert the workspace state and saved JSON both updated.
    seed = {
        "version": 1,
        "tabs": [
            {
                "id": "main",
                "title": "Main",
                "layout": {
                    "version": 1,
                    "layout": {"id": "orch", "widget": "OrchestratorChat"},
                },
            },
            {
                "id": "logs",
                "title": "Logs",
                "layout": {
                    "version": 1,
                    "layout": {"id": "feed", "widget": "ActivityFeed"},
                },
            },
        ],
        "active": "main",
    }
    (tmp_path / ".patchfeld").mkdir()
    (tmp_path / ".patchfeld" / "workspace.json").write_text(json.dumps(seed))

    app = _build_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        tc = app.query_one("#app-tabs", TabbedContent)
        tc.active = "tab-logs"
        await pilot.pause()
        assert app._active_tab_id == "logs"
        ws_raw = json.loads((tmp_path / ".patchfeld" / "workspace.json").read_text())
        assert ws_raw["active"] == "logs"


@pytest.mark.asyncio
async def test_tab_activation_publishes_tab_switched_event(tmp_path):
    seed = {
        "version": 1,
        "tabs": [
            {"id": "main", "title": "Main",
             "layout": {"version": 1, "layout": {"id": "orch", "widget": "OrchestratorChat"}}},
            {"id": "logs", "title": "Logs",
             "layout": {"version": 1, "layout": {"id": "feed", "widget": "ActivityFeed"}}},
        ],
        "active": "main",
    }
    (tmp_path / ".patchfeld").mkdir()
    (tmp_path / ".patchfeld" / "workspace.json").write_text(json.dumps(seed))

    app = _build_app(tmp_path)
    seen: list = []
    from patchfeld.events import TabSwitched
    app.event_bus.subscribe(TabSwitched, lambda e: seen.append(e))

    async with app.run_test() as pilot:
        await pilot.pause()
        tc = app.query_one("#app-tabs", TabbedContent)
        tc.active = "tab-logs"
        await pilot.pause()

    assert any(e.tab_id == "logs" and e.title == "Logs" for e in seen)


@pytest.mark.asyncio
async def test_tab_widgets_persist_across_switches(tmp_path):
    """Stateful widgets (e.g., a Notebook scratch buffer) survive switches."""
    seed = {
        "version": 1,
        "tabs": [
            {"id": "main", "title": "Main",
             "layout": {"version": 1, "layout": {"id": "orch", "widget": "OrchestratorChat"}}},
            {"id": "scratch", "title": "Scratch",
             "layout": {
                 "version": 1,
                 "layout": {
                     "id": "note", "widget": "Notebook",
                     "props": {"name": "memo"},
                 },
             }},
        ],
        "active": "main",
    }
    (tmp_path / ".patchfeld").mkdir()
    (tmp_path / ".patchfeld" / "workspace.json").write_text(json.dumps(seed))

    app = _build_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        # The Notebook in the inactive tab is mounted from launch.
        notebook = app.query_one("#panel-note")
        assert notebook is not None
        # Switch to it, switch away, switch back — same widget instance.
        tc = app.query_one("#app-tabs", TabbedContent)
        tc.active = "tab-scratch"
        await pilot.pause()
        same = app.query_one("#panel-note")
        assert same is notebook
        tc.active = "tab-main"
        await pilot.pause()
        assert app.query_one("#panel-note") is notebook
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_app_smoke_tabs.py -v -k "activation or persist"`
Expected: FAIL — `_active_tab_id` doesn't update on switch; no `TabSwitched` is published.

- [ ] **Step 3: Wire the `TabActivated` handler in `patchfeld/app.py`**

Add the import at the top:

```python
from patchfeld.events import LayoutApplied, TabAdded, TabClosed, TabSwitched
```

Add a handler method on `PatchfeldApp`:

```python
def on_tabbed_content_tab_activated(
    self, event: TabbedContent.TabActivated,
) -> None:
    """Triggered by TabbedContent when the user (or code) switches the active
    pane. Updates workspace state, persists, fires our TabSwitched event, and
    restores focus to the tab's last-focused panel id."""
    if self._workspace is None:
        return
    pane_id = event.tab.id  # like "tab-<tab_id>"
    if not pane_id or not pane_id.startswith("tab-"):
        return
    new_active = pane_id[len("tab-"):]
    if new_active == self._active_tab_id:
        return
    # Snapshot focus on the tab we're leaving.
    if self._active_tab_id is not None:
        try:
            focused = self.focused
            if focused is not None and focused.id and focused.id.startswith("panel-"):
                self._tab_focus_snapshots[self._active_tab_id] = focused.id[len("panel-"):]
        except Exception:
            pass
    self._active_tab_id = new_active
    self._workspace = self._workspace.model_copy(update={"active": new_active})
    save_local_workspace(self.cwd, self._workspace)
    title = next((t.title for t in self._workspace.tabs if t.id == new_active), new_active)
    self.event_bus.publish(TabSwitched(tab_id=new_active, title=title))
    # Restore focus on the tab we're entering.
    target_tab = next((t for t in self._workspace.tabs if t.id == new_active), None)
    target_panel_id = (
        self._tab_focus_snapshots.get(new_active)
        or (target_tab.layout.focus if target_tab else None)
    )
    if target_panel_id:
        try:
            self.query_one(f"#panel-{target_panel_id}").focus()
        except Exception:
            pass
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_app_smoke_tabs.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add patchfeld/app.py tests/test_app_smoke_tabs.py
git commit -m "feat(app): handle TabActivated — persist + publish TabSwitched + restore focus"
```

---

## Task 10: `add_tab` MCP tool

**Files:**
- Create: `patchfeld/orchestrator/tabs_tools.py`
- Test: `tests/test_orchestrator_tabs_tools.py`

For all four tab-tool tasks (10–13) we route through a small workspace-mutation surface on `PatchfeldApp`. We add the first method here.

- [ ] **Step 1: Write the failing test**

Create `tests/test_orchestrator_tabs_tools.py`:

```python
import json
import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock
from textual.widgets import TabbedContent, TabPane

from patchfeld.agents.fake_sdk_adapter import FakeSDKAdapter
from patchfeld.agents.manager import AgentManager
from patchfeld.app import PatchfeldApp
from patchfeld.events import EventBus
from patchfeld.orchestrator.session import OrchestratorSession
from patchfeld.orchestrator.tabs_tools import (
    add_tab_handler,
    close_tab_handler,
    list_tabs_handler,
    switch_tab_handler,
)


def _ok():
    return [
        AssistantMessage(content=[TextBlock(text="ok")], model="fake-model"),
        ResultMessage(
            subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
            num_turns=1, session_id="fake", total_cost_usd=0.0,
            usage={"input_tokens": 1, "output_tokens": 1}, result="ok",
        ),
    ]


def _build_app(tmp_path):
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
    )
    return app


@pytest.mark.asyncio
async def test_add_tab_with_default_layout(tmp_path):
    app = _build_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        handler = add_tab_handler(app)
        result = await handler({"title": "Logs"})
        await pilot.pause()
        body = json.loads(result["content"][0]["text"])
        assert body["title"] == "Logs"
        assert "tab_id" in body
        # Default seed when workspace already has chat: ActivityFeed-only.
        new_tab = next(t for t in app._workspace.tabs if t.id == body["tab_id"])
        assert new_tab.layout.layout.widget == "ActivityFeed"
        # Activated by default.
        assert app._active_tab_id == body["tab_id"]
        tc = app.query_one("#app-tabs", TabbedContent)
        assert any(p.id == f"tab-{body['tab_id']}" for p in tc.query(TabPane))


@pytest.mark.asyncio
async def test_add_tab_with_inline_layout(tmp_path):
    app = _build_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        handler = add_tab_handler(app)
        layout = {
            "version": 1,
            "layout": {
                "type": "horizontal",
                "children": [
                    {"id": "tree", "widget": "FileTree", "props": {"path": "."}},
                    {"id": "view", "widget": "FileViewer",
                     "props": {"follow_selection": True}},
                ],
            },
        }
        result = await handler({"title": "Code", "layout": layout})
        await pilot.pause()
        body = json.loads(result["content"][0]["text"])
        new_tab = next(t for t in app._workspace.tabs if t.id == body["tab_id"])
        # Container with two children
        assert new_tab.layout.layout.children[0].widget == "FileTree"


@pytest.mark.asyncio
async def test_add_tab_with_named_layout_resolves(tmp_path):
    app = _build_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Save a named layout, then ask add_tab to seed from it by name.
        from patchfeld.layout.spec import LayoutSpec
        named = LayoutSpec.model_validate({
            "version": 1,
            "layout": {"id": "feed", "widget": "ActivityFeed"},
        })
        app.layouts_store.save("monitoring", named)
        handler = add_tab_handler(app)
        result = await handler({"title": "Monitoring", "layout": "monitoring"})
        await pilot.pause()
        body = json.loads(result["content"][0]["text"])
        new_tab = next(t for t in app._workspace.tabs if t.id == body["tab_id"])
        assert new_tab.layout.layout.widget == "ActivityFeed"


@pytest.mark.asyncio
async def test_add_tab_does_not_activate_when_activate_false(tmp_path):
    app = _build_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        original_active = app._active_tab_id
        handler = add_tab_handler(app)
        await handler({"title": "Background", "activate": False})
        await pilot.pause()
        assert app._active_tab_id == original_active


@pytest.mark.asyncio
async def test_add_tab_publishes_tab_added_event(tmp_path):
    app = _build_app(tmp_path)
    seen: list = []
    from patchfeld.events import TabAdded
    app.event_bus.subscribe(TabAdded, lambda e: seen.append(e))
    async with app.run_test() as pilot:
        await pilot.pause()
        handler = add_tab_handler(app)
        await handler({"title": "Logs"})
        await pilot.pause()
    assert any(e.title == "Logs" for e in seen)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_orchestrator_tabs_tools.py -v -k "add_tab"`
Expected: FAIL — module `tabs_tools` doesn't exist.

- [ ] **Step 3: Add the workspace-mutation surface to `PatchfeldApp`**

In `patchfeld/app.py`, add these methods on `PatchfeldApp`. They are the single source of truth that all four tab tools (and the hotkeys later) call into:

```python
import secrets
from patchfeld.events import TabAdded, TabClosed
from patchfeld.layout.spec import Panel as _Panel


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
        from patchfeld.workspace.spec import _contains_chat
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


async def add_tab(self, title: str, layout: LayoutSpec, *, activate: bool = True) -> str:
    """Append a new tab. Returns the new tab id. Updates persistence."""
    if self._workspace is None:
        raise RuntimeError("workspace not yet initialized")
    new_id = self._generate_tab_id()
    new_tab = Tab(id=new_id, title=title, layout=layout)
    self._workspace = Workspace.model_validate({
        "version": self._workspace.version,
        "tabs": [t.model_dump(mode="json") for t in self._workspace.tabs] + [new_tab.model_dump(mode="json")],
        "active": new_id if activate else self._workspace.active,
    })
    # Mount the new pane and apply its layout.
    tc = self.query_one("#app-tabs", TabbedContent)
    pane = TabPane(title, TxContainer(id=f"panel-area-{new_id}"), id=f"tab-{new_id}")
    await tc.add_pane(pane)
    area = self.query_one(f"#panel-area-{new_id}", TxContainer)
    await apply_layout(area, layout, self.registry, layout_name=None)
    if activate:
        tc.active = f"tab-{new_id}"
        self._active_tab_id = new_id
    save_local_workspace(self.cwd, self._workspace)
    self.event_bus.publish(TabAdded(tab_id=new_id, title=title))
    return new_id
```

- [ ] **Step 4: Implement `add_tab_handler`**

Create `patchfeld/orchestrator/tabs_tools.py`:

```python
import json
from typing import Any

from patchfeld.layout.spec import LayoutSpec


def _ok(payload: dict) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(payload, indent=2)}]}


def _err(message: str, **extra: Any) -> dict:
    body = {"error": message}
    body.update(extra)
    return {"content": [{"type": "text", "text": json.dumps(body, indent=2)}]}


def add_tab_handler(app):
    """Build an MCP handler that delegates to app.add_tab.

    args:
        title: tab strip label (required).
        layout: optional. Either a LayoutSpec dict, or a string naming a
                saved layout in NamedLayoutsStore. If None, a default seed
                is used (chat-only if the workspace has no chat yet, else
                ActivityFeed-only).
        activate: bool, default True.
    """
    async def add_tab_tool(args: dict) -> dict:
        title = args.get("title")
        if not title or not isinstance(title, str):
            return _err("`title` is required and must be a string")
        raw_layout = args.get("layout")
        activate = bool(args.get("activate", True))
        try:
            if raw_layout is None:
                layout = app._default_seed_layout()
            elif isinstance(raw_layout, str):
                spec = app.layouts_store.load(raw_layout)
                if spec is None:
                    return _err(f"named layout not found: {raw_layout}",
                                suggestion="call list_layouts to see available names")
                layout = spec
            elif isinstance(raw_layout, dict):
                layout = LayoutSpec.model_validate(raw_layout)
            else:
                return _err("`layout` must be a dict, a string name, or omitted")
        except Exception as e:
            return _err(f"invalid layout: {e}")
        try:
            tab_id = await app.add_tab(title, layout, activate=activate)
        except Exception as e:
            return _err(f"add_tab failed: {e}")
        return _ok({"tab_id": tab_id, "title": title, "active": activate})


    # placeholder for the other handlers — added in Tasks 11-13
    return add_tab_tool


def close_tab_handler(app):
    """Stub — implemented in Task 11."""
    raise NotImplementedError


def switch_tab_handler(app):
    """Stub — implemented in Task 12."""
    raise NotImplementedError


def list_tabs_handler(app):
    """Stub — implemented in Task 13."""
    raise NotImplementedError
```

- [ ] **Step 5: Run add_tab tests to verify they pass**

Run: `uv run pytest tests/test_orchestrator_tabs_tools.py -v -k "add_tab"`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add patchfeld/app.py patchfeld/orchestrator/tabs_tools.py tests/test_orchestrator_tabs_tools.py
git commit -m "feat(orchestrator): add_tab MCP tool with default/inline/named layout seeding"
```

---

## Task 11: `close_tab` MCP tool

**Files:**
- Modify: `patchfeld/orchestrator/tabs_tools.py`
- Modify: `patchfeld/app.py`
- Test: `tests/test_orchestrator_tabs_tools.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_orchestrator_tabs_tools.py`:

```python
@pytest.mark.asyncio
async def test_close_tab_removes_pane_and_updates_workspace(tmp_path):
    app = _build_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Add a second tab so close has something to remove.
        add = add_tab_handler(app)
        result = await add({"title": "Logs"})
        new_id = json.loads(result["content"][0]["text"])["tab_id"]
        await pilot.pause()

        close = close_tab_handler(app)
        result = await close({"tab_id": new_id})
        await pilot.pause()
        body = json.loads(result["content"][0]["text"])
        assert body["closed"] == new_id
        assert all(t.id != new_id for t in app._workspace.tabs)
        tc = app.query_one("#app-tabs", TabbedContent)
        assert all(p.id != f"tab-{new_id}" for p in tc.query(TabPane))


@pytest.mark.asyncio
async def test_close_tab_refuses_to_close_last_tab(tmp_path):
    app = _build_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        only_id = app._workspace.tabs[0].id
        close = close_tab_handler(app)
        result = await close({"tab_id": only_id})
        body = json.loads(result["content"][0]["text"])
        assert body["error"] == "would_leave_zero_tabs"
        assert any(t.id == only_id for t in app._workspace.tabs)


@pytest.mark.asyncio
async def test_close_tab_refuses_when_no_chat_remains(tmp_path):
    # Seed a workspace where tab "main" has chat and tab "logs" doesn't.
    seed = {
        "version": 1,
        "tabs": [
            {"id": "main", "title": "Main",
             "layout": {"version": 1, "layout": {"id": "orch", "widget": "OrchestratorChat"}}},
            {"id": "logs", "title": "Logs",
             "layout": {"version": 1, "layout": {"id": "feed", "widget": "ActivityFeed"}}},
        ],
        "active": "main",
    }
    (tmp_path / ".patchfeld").mkdir()
    (tmp_path / ".patchfeld" / "workspace.json").write_text(json.dumps(seed))
    app = _build_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        close = close_tab_handler(app)
        result = await close({"tab_id": "main"})
        body = json.loads(result["content"][0]["text"])
        assert body["error"] == "would_leave_no_chat"
        assert any(t.id == "main" for t in app._workspace.tabs)


@pytest.mark.asyncio
async def test_close_tab_unknown_id_returns_error(tmp_path):
    app = _build_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        close = close_tab_handler(app)
        result = await close({"tab_id": "ghost"})
        body = json.loads(result["content"][0]["text"])
        assert body["error"] == "unknown_tab_id"


@pytest.mark.asyncio
async def test_close_tab_publishes_tab_closed_event(tmp_path):
    app = _build_app(tmp_path)
    seen: list = []
    from patchfeld.events import TabClosed
    app.event_bus.subscribe(TabClosed, lambda e: seen.append(e))
    async with app.run_test() as pilot:
        await pilot.pause()
        add = add_tab_handler(app)
        result = await add({"title": "Logs"})
        new_id = json.loads(result["content"][0]["text"])["tab_id"]
        await pilot.pause()
        close = close_tab_handler(app)
        await close({"tab_id": new_id})
        await pilot.pause()
    assert any(e.tab_id == new_id for e in seen)


@pytest.mark.asyncio
async def test_close_active_tab_falls_back_to_neighbor(tmp_path):
    app = _build_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        add = add_tab_handler(app)
        r1 = await add({"title": "Logs", "activate": True})
        new_id = json.loads(r1["content"][0]["text"])["tab_id"]
        await pilot.pause()
        assert app._active_tab_id == new_id
        close = close_tab_handler(app)
        await close({"tab_id": new_id})
        await pilot.pause()
        # Active falls back to the previous tab.
        assert app._active_tab_id != new_id
        assert app._active_tab_id is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_orchestrator_tabs_tools.py -v -k "close_tab"`
Expected: FAIL — `close_tab_handler` raises `NotImplementedError`.

- [ ] **Step 3: Add `close_tab` to `PatchfeldApp`**

In `patchfeld/app.py`, add:

```python
from patchfeld.workspace.spec import _contains_chat


async def close_tab(self, tab_id: str) -> dict:
    """Close a tab. Returns a small result dict; never raises on bad input."""
    if self._workspace is None:
        return {"error": "workspace_not_initialized"}
    tabs = self._workspace.tabs
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
        "version": self._workspace.version,
        "tabs": [t.model_dump(mode="json") for t in remaining],
        "active": new_active,
    })
    self._tab_focus_snapshots.pop(tab_id, None)
    tc = self.query_one("#app-tabs", TabbedContent)
    await tc.remove_pane(f"tab-{tab_id}")
    if self._active_tab_id == tab_id:
        self._active_tab_id = new_active
        tc.active = f"tab-{new_active}"
    save_local_workspace(self.cwd, self._workspace)
    self.event_bus.publish(TabClosed(tab_id=tab_id))
    return {"closed": tab_id, "new_active": new_active}
```

- [ ] **Step 4: Implement `close_tab_handler`**

Replace the stub in `patchfeld/orchestrator/tabs_tools.py`:

```python
def close_tab_handler(app):
    async def close_tab_tool(args: dict) -> dict:
        tab_id = args.get("tab_id")
        if not isinstance(tab_id, str) or not tab_id:
            return _err("`tab_id` is required and must be a string")
        result = await app.close_tab(tab_id)
        if "error" in result:
            return _err(result["error"], **{k: v for k, v in result.items() if k != "error"})
        return _ok(result)

    return close_tab_tool
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_orchestrator_tabs_tools.py -v -k "close_tab"`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add patchfeld/app.py patchfeld/orchestrator/tabs_tools.py tests/test_orchestrator_tabs_tools.py
git commit -m "feat(orchestrator): close_tab tool with chat-invariant + last-tab guards"
```

---

## Task 12: `switch_tab` MCP tool

**Files:**
- Modify: `patchfeld/orchestrator/tabs_tools.py`
- Test: `tests/test_orchestrator_tabs_tools.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_orchestrator_tabs_tools.py`:

```python
@pytest.mark.asyncio
async def test_switch_tab_changes_active(tmp_path):
    app = _build_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        add = add_tab_handler(app)
        r = await add({"title": "Logs", "activate": False})
        new_id = json.loads(r["content"][0]["text"])["tab_id"]
        await pilot.pause()
        original_active = app._active_tab_id

        switch = switch_tab_handler(app)
        result = await switch({"tab_id": new_id})
        await pilot.pause()
        body = json.loads(result["content"][0]["text"])
        assert body["active"] == new_id
        assert app._active_tab_id == new_id
        assert app._active_tab_id != original_active


@pytest.mark.asyncio
async def test_switch_tab_unknown_id_returns_error(tmp_path):
    app = _build_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        switch = switch_tab_handler(app)
        result = await switch({"tab_id": "ghost"})
        body = json.loads(result["content"][0]["text"])
        assert body["error"] == "unknown_tab_id"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_orchestrator_tabs_tools.py -v -k "switch_tab"`
Expected: FAIL — `switch_tab_handler` raises `NotImplementedError`.

- [ ] **Step 3: Implement `switch_tab_handler`**

Replace the stub in `patchfeld/orchestrator/tabs_tools.py`:

```python
def switch_tab_handler(app):
    async def switch_tab_tool(args: dict) -> dict:
        tab_id = args.get("tab_id")
        if not isinstance(tab_id, str) or not tab_id:
            return _err("`tab_id` is required and must be a string")
        if app._workspace is None or all(t.id != tab_id for t in app._workspace.tabs):
            return _err("unknown_tab_id")
        from textual.widgets import TabbedContent
        tc = app.query_one("#app-tabs", TabbedContent)
        tc.active = f"tab-{tab_id}"
        # The TabActivated handler updates _active_tab_id + persistence + event.
        return _ok({"active": tab_id})

    return switch_tab_tool
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_orchestrator_tabs_tools.py -v -k "switch_tab"`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add patchfeld/orchestrator/tabs_tools.py tests/test_orchestrator_tabs_tools.py
git commit -m "feat(orchestrator): switch_tab tool"
```

---

## Task 13: `list_tabs` MCP tool

**Files:**
- Modify: `patchfeld/orchestrator/tabs_tools.py`
- Test: `tests/test_orchestrator_tabs_tools.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_orchestrator_tabs_tools.py`:

```python
@pytest.mark.asyncio
async def test_list_tabs_returns_metadata(tmp_path):
    app = _build_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        add = add_tab_handler(app)
        await add({"title": "Logs", "activate": False})
        await pilot.pause()
        ls = list_tabs_handler(app)
        result = await ls({})
        body = json.loads(result["content"][0]["text"])
        assert isinstance(body, list)
        assert len(body) == 2
        # First tab is the seeded default; assert structure of one entry.
        for entry in body:
            assert {"id", "title", "active", "has_chat", "panel_ids"} <= set(entry.keys())
        assert sum(1 for t in body if t["active"]) == 1


@pytest.mark.asyncio
async def test_list_tabs_panel_ids_include_panels_in_panel_tabs(tmp_path):
    app = _build_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        add = add_tab_handler(app)
        await add({
            "title": "Mixed",
            "layout": {
                "version": 1,
                "layout": {
                    "type": "tabs",
                    "children": [
                        {"id": "feed", "widget": "ActivityFeed"},
                        {"id": "logs", "widget": "LogTail"},
                    ],
                },
            },
            "activate": False,
        })
        await pilot.pause()
        ls = list_tabs_handler(app)
        result = await ls({})
        body = json.loads(result["content"][0]["text"])
        mixed = next(t for t in body if t["title"] == "Mixed")
        assert set(mixed["panel_ids"]) == {"feed", "logs"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_orchestrator_tabs_tools.py -v -k "list_tabs"`
Expected: FAIL — `list_tabs_handler` raises `NotImplementedError`.

- [ ] **Step 3: Implement `list_tabs_handler`**

Replace the stub in `patchfeld/orchestrator/tabs_tools.py`. Add this helper at the top of the module:

```python
def _panel_ids(node) -> list[str]:
    from patchfeld.layout.spec import Container, Panel, Tabs
    if isinstance(node, Panel):
        return [node.id]
    if isinstance(node, Tabs):
        return [c.id for c in node.children]
    if isinstance(node, Container):
        out: list[str] = []
        for c in node.children:
            out.extend(_panel_ids(c))
        return out
    return []


def _has_chat(node) -> bool:
    from patchfeld.workspace.spec import _contains_chat
    return _contains_chat(node)
```

And implement the handler:

```python
def list_tabs_handler(app):
    async def list_tabs_tool(_args: dict) -> dict:
        if app._workspace is None:
            return _ok([])
        out = []
        for t in app._workspace.tabs:
            out.append({
                "id": t.id,
                "title": t.title,
                "active": (t.id == app._active_tab_id),
                "has_chat": _has_chat(t.layout.layout),
                "panel_ids": _panel_ids(t.layout.layout),
            })
        return _ok(out)

    return list_tabs_tool
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_orchestrator_tabs_tools.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add patchfeld/orchestrator/tabs_tools.py tests/test_orchestrator_tabs_tools.py
git commit -m "feat(orchestrator): list_tabs tool with chat + panel_ids metadata"
```

---

## Task 14: Wire tab tools into `build_orchestrator_tools` and the MCP server

**Files:**
- Modify: `patchfeld/orchestrator/tools.py`
- Modify: `patchfeld/orchestrator/session.py` (only if it constructs tools — verify)
- Modify: `patchfeld/app.py` (pass `app` reference into orchestrator)
- Test: `tests/test_orchestrator_tools.py` or add a small registration test

- [ ] **Step 1: Read `patchfeld/orchestrator/session.py` to find how `build_orchestrator_tools` / `build_orchestrator_mcp_server` is called**

Run: `grep -n "build_orchestrator" patchfeld/orchestrator/session.py patchfeld/app.py`

Note the call site(s); the exact name of the parameter `OrchestratorSession.__init__` accepts (e.g. an `app` reference) determines what we pass through.

- [ ] **Step 2: Write the failing test**

Append to `tests/test_orchestrator_tools.py` (the file exists; if not, create it with a minimal smoke test):

```python
import pytest
from patchfeld.agents.fake_sdk_adapter import FakeSDKAdapter
from patchfeld.agents.manager import AgentManager
from patchfeld.app import PatchfeldApp
from patchfeld.events import EventBus
from patchfeld.orchestrator.session import OrchestratorSession
from patchfeld.orchestrator.tools import build_orchestrator_tools


@pytest.mark.asyncio
async def test_build_orchestrator_tools_includes_tab_tools(tmp_path):
    bus = EventBus()
    manager = AgentManager(cwd=tmp_path, bus=bus,
                           adapter_factory=lambda: FakeSDKAdapter(scripts=[]))
    app = PatchfeldApp(cwd=tmp_path, manager=manager, global_dir=tmp_path)
    tools = build_orchestrator_tools(
        app.manager,
        apply_layout=app._orchestrator_apply_layout,
        layouts_store=app.layouts_store,
        config_store=app.config_store,
        actions=app.actions_registry,
        rebind_keys=app._rebind_keys,
        widget_registry=app.registry,
        current_layout=lambda: app._active_layout(),
        app=app,
    )
    assert "add_tab" in tools
    assert "close_tab" in tools
    assert "switch_tab" in tools
    assert "list_tabs" in tools
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_orchestrator_tools.py::test_build_orchestrator_tools_includes_tab_tools -v`
Expected: FAIL — `build_orchestrator_tools` doesn't accept `app=` kwarg.

- [ ] **Step 4: Add the wiring in `patchfeld/orchestrator/tools.py`**

At the top of `patchfeld/orchestrator/tools.py`, add:

```python
from patchfeld.orchestrator.tabs_tools import (
    add_tab_handler,
    close_tab_handler,
    list_tabs_handler,
    switch_tab_handler,
)
```

Update `build_orchestrator_tools`'s signature to accept `app=None` and register the four handlers when `app is not None`:

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
    app=None,
):
    handlers: dict = {}
    for spec in _SPECS:
        handlers[spec.name] = spec.build(manager)
    if apply_layout is not None and layouts_store is not None:
        handlers["set_layout"] = _set_layout_handler(apply_layout, widget_registry)
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
    if widget_registry is not None and current_layout is not None:
        handlers["get_layout"] = _get_layout_handler(current_layout, widget_registry)
    if app is not None:
        handlers["add_tab"] = add_tab_handler(app)
        handlers["close_tab"] = close_tab_handler(app)
        handlers["switch_tab"] = switch_tab_handler(app)
        handlers["list_tabs"] = list_tabs_handler(app)
    return handlers
```

Mirror the addition in `build_orchestrator_mcp_server` — accept `app=None` and register the four `tool(...)` decorators with the descriptions below:

```python
    if app is not None:
        sdk_tools.append(tool(
            "add_tab",
            "Create a new app-level tab. `title` is the user-facing label "
            "on the tab strip. Optional `layout` may be a LayoutSpec dict, "
            "the name of a saved layout (resolved from the named-layouts "
            "store), or omitted (a default seed is used). Optional "
            "`activate` (default true) makes the new tab the active one. "
            "Returns the new tab id.",
            {"title": str, "layout": dict, "activate": bool},
        )(add_tab_handler(app)))
        sdk_tools.append(tool(
            "close_tab",
            "Close the tab with the given id. Refuses if it would leave "
            "the workspace with zero OrchestratorChat panels (returns a "
            "structured error so you can add chat to another tab first). "
            "Refuses if it's the last tab.",
            {"tab_id": str},
        )(close_tab_handler(app)))
        sdk_tools.append(tool(
            "switch_tab",
            "Make the tab with the given id the active one.",
            {"tab_id": str},
        )(switch_tab_handler(app)))
        sdk_tools.append(tool(
            "list_tabs",
            "List all tabs with id, title, active flag, has_chat flag, "
            "and the list of panel ids contained in each tab.",
            {},
        )(list_tabs_handler(app)))
```

- [ ] **Step 5: Pass `app` from the `OrchestratorSession` construction site**

In `patchfeld/app.py`'s `__init__`, update the `OrchestratorSession(...)` call to also pass `app=self`. In `patchfeld/orchestrator/session.py`, accept an `app` kwarg in `OrchestratorSession.__init__` and pass it through to `build_orchestrator_mcp_server` (and `build_orchestrator_tools` if it's also called there).

(If `OrchestratorSession` doesn't currently take `app`, add the kwarg with a default of `None` and store it on `self._app`. Forward to whichever build call constructs the SDK tools.)

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_orchestrator_tools.py -v -k "tab_tools"`
Expected: PASS.

Run the broader orchestrator-tools suite to catch regressions:

Run: `uv run pytest tests/test_orchestrator_tools.py tests/test_orchestrator_tools_layout.py tests/test_orchestrator_tools_get_layout.py -v`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add patchfeld/orchestrator/tools.py patchfeld/orchestrator/session.py patchfeld/app.py tests/test_orchestrator_tools.py
git commit -m "feat(orchestrator): wire add_tab/close_tab/switch_tab/list_tabs into MCP server"
```

---

## Task 15: `set_layout` / `get_layout` / `save_layout` / `load_layout` accept `tab_id`; `load_layout` accepts `as_new_tab`

**Files:**
- Modify: `patchfeld/orchestrator/tools.py`
- Modify: `patchfeld/app.py` (extend `_orchestrator_apply_layout` to accept `tab_id`)
- Test: `tests/test_orchestrator_tools_layout.py`, `tests/test_orchestrator_tools_get_layout.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_orchestrator_tools_layout.py`:

```python
import json
import pytest

from patchfeld.agents.fake_sdk_adapter import FakeSDKAdapter
from patchfeld.agents.manager import AgentManager
from patchfeld.app import PatchfeldApp
from patchfeld.events import EventBus
from patchfeld.orchestrator.session import OrchestratorSession
from patchfeld.orchestrator.tabs_tools import add_tab_handler
from patchfeld.orchestrator.tools import build_orchestrator_tools


def _ok():
    from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock
    return [
        AssistantMessage(content=[TextBlock(text="ok")], model="fake-model"),
        ResultMessage(
            subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
            num_turns=1, session_id="fake", total_cost_usd=0.0,
            usage={"input_tokens": 1, "output_tokens": 1}, result="ok",
        ),
    ]


def _build(tmp_path):
    bus = EventBus()
    manager = AgentManager(cwd=tmp_path, bus=bus,
                           adapter_factory=lambda: FakeSDKAdapter(scripts=[_ok()]))
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
        app=app,
    )
    return app


@pytest.mark.asyncio
async def test_set_layout_targets_active_tab_by_default(tmp_path):
    app = _build(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        tools = build_orchestrator_tools(
            app.manager,
            apply_layout=app._orchestrator_apply_layout,
            layouts_store=app.layouts_store,
            widget_registry=app.registry,
            current_layout=lambda: app._active_layout(),
            app=app,
        )
        new_layout = {
            "version": 1,
            "layout": {
                "type": "horizontal",
                "children": [
                    {"id": "orch", "widget": "OrchestratorChat"},
                    {"id": "feed", "widget": "ActivityFeed"},
                ],
            },
        }
        result = await tools["set_layout"]({"spec": new_layout})
        await pilot.pause()
        active_tab = next(t for t in app._workspace.tabs if t.id == app._active_tab_id)
        assert active_tab.layout.layout.children[1].widget == "ActivityFeed"


@pytest.mark.asyncio
async def test_set_layout_with_tab_id_targets_specific_tab(tmp_path):
    app = _build(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        add = add_tab_handler(app)
        r = await add({"title": "Logs", "activate": False})
        new_id = json.loads(r["content"][0]["text"])["tab_id"]
        await pilot.pause()
        original_active = app._active_tab_id

        tools = build_orchestrator_tools(
            app.manager,
            apply_layout=app._orchestrator_apply_layout,
            layouts_store=app.layouts_store,
            widget_registry=app.registry,
            current_layout=lambda: app._active_layout(),
            app=app,
        )
        new_layout = {
            "version": 1,
            "layout": {"id": "tail", "widget": "LogTail",
                       "props": {"file_path": "/tmp/x.log"}},
        }
        await tools["set_layout"]({"spec": new_layout, "tab_id": new_id})
        await pilot.pause()
        target = next(t for t in app._workspace.tabs if t.id == new_id)
        assert target.layout.layout.widget == "LogTail"
        # Active didn't change.
        assert app._active_tab_id == original_active


@pytest.mark.asyncio
async def test_get_layout_includes_tab_metadata(tmp_path):
    app = _build(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        tools = build_orchestrator_tools(
            app.manager,
            apply_layout=app._orchestrator_apply_layout,
            layouts_store=app.layouts_store,
            widget_registry=app.registry,
            current_layout=lambda: app._active_layout(),
            app=app,
        )
        result = await tools["get_layout"]({})
        body = json.loads(result["content"][0]["text"])
        assert body["tab_id"] == app._active_tab_id
        assert "tab_title" in body
        assert "spec" in body  # the LayoutSpec dump


@pytest.mark.asyncio
async def test_load_layout_as_new_tab_creates_a_tab(tmp_path):
    app = _build(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        from patchfeld.layout.spec import LayoutSpec
        named = LayoutSpec.model_validate({
            "version": 1, "layout": {"id": "feed", "widget": "ActivityFeed"},
        })
        app.layouts_store.save("monitoring", named)

        tools = build_orchestrator_tools(
            app.manager,
            apply_layout=app._orchestrator_apply_layout,
            layouts_store=app.layouts_store,
            widget_registry=app.registry,
            current_layout=lambda: app._active_layout(),
            app=app,
        )
        before = len(app._workspace.tabs)
        result = await tools["load_layout"]({"name": "monitoring", "as_new_tab": True})
        await pilot.pause()
        body_text = result["content"][0]["text"]
        assert len(app._workspace.tabs) == before + 1
        assert "monitoring" in body_text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_orchestrator_tools_layout.py -v -k "set_layout or get_layout or load_layout_as_new_tab"`
Expected: FAIL — current implementation ignores `tab_id`, doesn't include tab metadata, doesn't accept `as_new_tab`.

- [ ] **Step 3: Make `_orchestrator_apply_layout` accept `tab_id`**

In `patchfeld/app.py`:

```python
async def _orchestrator_apply_layout(
    self, spec: LayoutSpec,
    *, layout_name: str | None = None, tab_id: str | None = None,
) -> None:
    target = tab_id or self._active_tab_id
    await self._apply_to_tab(target, spec, layout_name=layout_name)
```

- [ ] **Step 4: Update `_set_layout_handler` and `_get_layout_handler`**

In `patchfeld/orchestrator/tools.py`, replace `_set_layout_handler` to forward `tab_id`:

```python
def _set_layout_handler(apply_layout, widget_registry=None):
    from patchfeld.layout.custom_widgets import register_custom_widget, CustomWidgetError

    async def set_layout_tool(args: dict) -> dict:
        try:
            spec = LayoutSpec.model_validate(args["spec"])
        except Exception as e:
            return {"content": [{"type": "text", "text": f"Invalid LayoutSpec: {e}"}]}
        if spec.custom_widgets and widget_registry is not None:
            for cw in spec.custom_widgets:
                try:
                    register_custom_widget(widget_registry, cw.name, cw.source)
                except CustomWidgetError as e:
                    return {"content": [{"type": "text",
                                         "text": f"Custom widget {cw.name!r} error: {e}"}]}
        try:
            await apply_layout(spec, tab_id=args.get("tab_id"))
        except Exception as e:
            return {"content": [{"type": "text", "text": f"Apply error: {e}"}]}
        return {"content": [{"type": "text", "text": "Layout applied."}]}
    return set_layout_tool
```

Replace `_get_layout_handler` to include tab metadata. Change its signature so it accepts an `app` reference for tab lookup:

```python
def _get_layout_handler(current_layout, widget_registry: WidgetRegistry, app=None):
    from patchfeld.layout.titles import populate_effective_titles

    async def get_layout_tool(args: dict) -> dict:
        target_tab_id = (args or {}).get("tab_id")
        spec = None
        tab_title = None
        tab_id = None
        if app is not None:
            ws = getattr(app, "_workspace", None)
            tid = target_tab_id or getattr(app, "_active_tab_id", None)
            if ws is not None and tid is not None:
                tab = next((t for t in ws.tabs if t.id == tid), None)
                if tab is not None:
                    spec = tab.layout
                    tab_id = tab.id
                    tab_title = tab.title
        if spec is None:
            spec = current_layout() if current_layout is not None else None
        if spec is None:
            return {"content": [{"type": "text", "text": "No layout applied yet."}]}
        dumped = spec.model_dump(mode="json")
        try:
            populate_effective_titles(dumped["layout"], widget_registry)
        except Exception:
            pass
        out = {"tab_id": tab_id, "tab_title": tab_title, "spec": dumped}
        return {"content": [{"type": "text", "text": json.dumps(out, indent=2)}]}

    return get_layout_tool
```

Update the dict-builder and MCP-server-builder call sites to pass `app=app`:

```python
    if widget_registry is not None and current_layout is not None:
        handlers["get_layout"] = _get_layout_handler(current_layout, widget_registry, app=app)
```

```python
    if widget_registry is not None and current_layout is not None:
        sdk_tools.append(tool(
            "get_layout",
            "Returns the active tab's LayoutSpec as JSON, alongside `tab_id` "
            "and `tab_title`. Each panel's `title` field is populated to its "
            "effective on-screen value. Pass `tab_id` to inspect a specific "
            "tab. Pass the `spec` field's value back through `set_layout` to "
            "edit the tab.",
            {"tab_id": str},
        )(_get_layout_handler(current_layout, widget_registry, app=app)))
```

Update the MCP-server `set_layout` description and schema:

```python
            (
                "set_layout",
                "Edit the **active** tab's layout (or pass `tab_id` to "
                "target a specific tab). Use add_tab to create new tabs "
                "instead of inserting OrchestratorChat panels. Each panel "
                "may set an optional `title` field; the user references "
                "panels by title in chat. Call get_layout first to "
                "discover effective titles. Spec format supports a new "
                "node type `{type: 'tabs', children: [Panel, ...], "
                "active: '<panel_id>'}` for panel-level tabs (each tab "
                "holds exactly one widget).",
                {"spec": dict, "tab_id": str},
                _set_layout_handler(apply_layout, widget_registry),
            ),
```

- [ ] **Step 5: Update `_save_layout_handler` and `_load_layout_handler`**

```python
def _save_layout_handler(layouts_store: NamedLayoutsStore, app=None):
    async def save_layout_tool(args: dict) -> dict:
        name = args["name"]
        # If `spec` provided, use it. Else if `tab_id` provided (or omitted -> active), pull from workspace.
        if "spec" in args:
            try:
                spec = LayoutSpec.model_validate(args["spec"])
            except Exception as e:
                return {"content": [{"type": "text", "text": f"Invalid LayoutSpec: {e}"}]}
        elif app is not None:
            tid = args.get("tab_id") or app._active_tab_id
            ws = app._workspace
            if ws is None or tid is None:
                return {"content": [{"type": "text", "text": "No tab to save."}]}
            tab = next((t for t in ws.tabs if t.id == tid), None)
            if tab is None:
                return {"content": [{"type": "text", "text": f"Unknown tab_id: {tid}"}]}
            spec = tab.layout
        else:
            return {"content": [{"type": "text", "text": "Provide `spec` or call from an app."}]}
        try:
            layouts_store.save(name, spec)
        except ValueError as e:
            return {"content": [{"type": "text", "text": f"Invalid layout name: {e}"}]}
        return {"content": [{"type": "text", "text": f"Saved layout {name!r}."}]}
    return save_layout_tool
```

```python
def _load_layout_handler(apply_layout, layouts_store: NamedLayoutsStore, app=None):
    async def load_layout_tool(args: dict) -> dict:
        name = args["name"]
        spec = layouts_store.load(name)
        if spec is None:
            return {"content": [{"type": "text", "text": f"Layout not found: {name}"}]}
        as_new_tab = bool(args.get("as_new_tab"))
        if as_new_tab and app is not None:
            try:
                tab_id = await app.add_tab(args.get("title", name), spec, activate=True)
            except Exception as e:
                return {"content": [{"type": "text", "text": f"add_tab failed: {e}"}]}
            return {"content": [{"type": "text",
                                 "text": f"Loaded {name!r} into new tab {tab_id}."}]}
        try:
            await apply_layout(spec, layout_name=name, tab_id=args.get("tab_id"))
        except Exception as e:
            return {"content": [{"type": "text", "text": f"Apply error: {e}"}]}
        return {"content": [{"type": "text", "text": f"Loaded layout {name!r}."}]}
    return load_layout_tool
```

Pass `app=app` at both call sites:

```python
    if apply_layout is not None and layouts_store is not None:
        handlers["set_layout"] = _set_layout_handler(apply_layout, widget_registry)
        handlers["save_layout"] = _save_layout_handler(layouts_store, app=app)
        handlers["load_layout"] = _load_layout_handler(apply_layout, layouts_store, app=app)
        handlers["list_layouts"] = _list_layouts_handler(layouts_store)
```

And in `build_orchestrator_mcp_server`'s `layout_specs`:

```python
            (
                "save_layout",
                "Save a LayoutSpec under a name in ~/.config/patchfeld/layouts/. "
                "If `spec` is omitted, saves the active tab's current layout "
                "(or the tab named by `tab_id`).",
                {"name": str, "spec": dict, "tab_id": str},
                _save_layout_handler(layouts_store, app=app),
            ),
            (
                "load_layout",
                "Load a saved layout by name and apply it. By default it "
                "replaces the active tab's spec. Pass `tab_id` to target a "
                "specific tab. Pass `as_new_tab: true` to create a new tab "
                "seeded from the named layout instead (use `title` to label "
                "the new tab; defaults to the layout name).",
                {"name": str, "tab_id": str, "as_new_tab": bool, "title": str},
                _load_layout_handler(apply_layout, layouts_store, app=app),
            ),
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_orchestrator_tools_layout.py tests/test_orchestrator_tools_get_layout.py -v`
Expected: all pass.

(If existing `get_layout` callers in older tests destructured the response as a top-level spec dump, update them to read from the `"spec"` key. Search: `grep -nR "get_layout" tests/` and adjust.)

- [ ] **Step 7: Commit**

```bash
git add patchfeld/app.py patchfeld/orchestrator/tools.py tests/test_orchestrator_tools_layout.py tests/test_orchestrator_tools_get_layout.py
git commit -m "feat(orchestrator): tab-aware set_layout/get_layout/save_layout/load_layout"
```

---

## Task 16: Hotkeys — `ctrl+pageup` / `ctrl+pagedown`, `ctrl+1`..`ctrl+9`

**Files:**
- Modify: `patchfeld/app.py`
- Test: `tests/test_app_smoke_tabs.py`

`ctrl+pageup` / `ctrl+pagedown` are handled by Textual's `TabbedContent` natively; the only thing to do is surface them in `?` help. The numeric ones are new.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_app_smoke_tabs.py`:

```python
@pytest.mark.asyncio
async def test_ctrl_2_switches_to_second_tab(tmp_path):
    seed = {
        "version": 1,
        "tabs": [
            {"id": "main", "title": "Main",
             "layout": {"version": 1, "layout": {"id": "orch", "widget": "OrchestratorChat"}}},
            {"id": "logs", "title": "Logs",
             "layout": {"version": 1, "layout": {"id": "feed", "widget": "ActivityFeed"}}},
        ],
        "active": "main",
    }
    (tmp_path / ".patchfeld").mkdir()
    (tmp_path / ".patchfeld" / "workspace.json").write_text(json.dumps(seed))
    app = _build_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("ctrl+2")
        await pilot.pause()
        assert app._active_tab_id == "logs"


@pytest.mark.asyncio
async def test_ctrl_5_with_only_two_tabs_is_noop(tmp_path):
    app = _build_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        original = app._active_tab_id
        await pilot.press("ctrl+5")
        await pilot.pause()
        assert app._active_tab_id == original
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_app_smoke_tabs.py -v -k "ctrl_2 or ctrl_5"`
Expected: FAIL — `ctrl+2` is unbound.

- [ ] **Step 3: Add bindings + handler in `patchfeld/app.py`**

Append to the `BINDINGS` list:

```python
    BINDINGS = [
        Binding("/", "focus_command_bar", "command bar", priority=True),
        Binding("ctrl+q", "quit", "quit"),
        Binding("ctrl+h", "open_history", "history"),
        Binding("ctrl+l", "open_layout_switcher", "layouts"),
        Binding("?", "show_help", "help"),
        # Tabs:
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
```

Add the handler:

```python
def action_switch_tab_index(self, idx: int) -> None:
    if self._workspace is None:
        return
    if idx < 0 or idx >= len(self._workspace.tabs):
        return  # quietly no-op when there's no tab at that index
    target = self._workspace.tabs[idx].id
    tc = self.query_one("#app-tabs", TabbedContent)
    tc.active = f"tab-{target}"
```

Update the help string in `action_show_help` to mention tab hotkeys:

```python
def action_show_help(self) -> None:
    self.notify(
        "/ command bar · ctrl-q quit · ctrl-h history · ctrl-l layouts · "
        "ctrl-pgup/pgdn prev/next tab · ctrl-1..9 tab N · ctrl-t new tab · "
        "ctrl-w close tab · ? help",
        title="keybindings",
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_app_smoke_tabs.py -v -k "ctrl_2 or ctrl_5"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add patchfeld/app.py tests/test_app_smoke_tabs.py
git commit -m "feat(app): ctrl+1..9 tab-switch hotkeys + help text"
```

---

## Task 17: Hotkeys — `ctrl+t` (new tab modal) and `ctrl+w` (close active tab)

**Files:**
- Create: `patchfeld/widgets/new_tab_screen.py`
- Modify: `patchfeld/app.py`
- Test: `tests/test_app_smoke_tabs.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_app_smoke_tabs.py`:

```python
@pytest.mark.asyncio
async def test_ctrl_t_opens_new_tab_modal_and_creates_tab(tmp_path):
    app = _build_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        before = len(app._workspace.tabs)
        await pilot.press("ctrl+t")
        await pilot.pause()
        # Modal mounted; type a title and submit.
        await pilot.press("L", "o", "g", "s", "enter")
        await pilot.pause()
        assert len(app._workspace.tabs) == before + 1
        assert app._workspace.tabs[-1].title == "Logs"


@pytest.mark.asyncio
async def test_ctrl_w_on_last_tab_is_noop(tmp_path):
    app = _build_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        before = len(app._workspace.tabs)
        await pilot.press("ctrl+w")
        await pilot.pause()
        assert len(app._workspace.tabs) == before


@pytest.mark.asyncio
async def test_ctrl_w_closes_active_tab(tmp_path):
    seed = {
        "version": 1,
        "tabs": [
            {"id": "main", "title": "Main",
             "layout": {"version": 1, "layout": {"id": "orch", "widget": "OrchestratorChat"}}},
            {"id": "logs", "title": "Logs",
             "layout": {"version": 1, "layout": {"id": "feed", "widget": "ActivityFeed"}}},
        ],
        "active": "logs",
    }
    (tmp_path / ".patchfeld").mkdir()
    (tmp_path / ".patchfeld" / "workspace.json").write_text(json.dumps(seed))
    app = _build_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("ctrl+w")
        await pilot.pause()
        assert all(t.id != "logs" for t in app._workspace.tabs)
        assert app._active_tab_id == "main"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_app_smoke_tabs.py -v -k "ctrl_t or ctrl_w"`
Expected: FAIL — bindings don't exist.

- [ ] **Step 3: Implement the new-tab modal screen**

Create `patchfeld/widgets/new_tab_screen.py`:

```python
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Static


class NewTabScreen(ModalScreen[str | None]):
    """Tiny modal that asks for a tab title and dismisses with the entered
    string (or None on escape)."""

    DEFAULT_CSS = """
    NewTabScreen { align: center middle; }
    NewTabScreen > Vertical {
        width: 50; height: auto; padding: 1 2;
        background: $surface; border: round $primary;
    }
    """

    BINDINGS = [("escape", "cancel", "cancel")]

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("New tab title:")
            yield Input(placeholder="e.g., Logs", id="new-tab-input")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        title = (event.value or "").strip()
        self.dismiss(title or None)

    def action_cancel(self) -> None:
        self.dismiss(None)
```

- [ ] **Step 4: Add `ctrl+t` and `ctrl+w` bindings + handlers in `patchfeld/app.py`**

Append to the `BINDINGS` list (after the tab-index entries):

```python
        Binding("ctrl+t", "new_tab", "new tab"),
        Binding("ctrl+w", "close_active_tab", "close tab"),
```

Add the handlers (and import the modal screen):

```python
from patchfeld.widgets.new_tab_screen import NewTabScreen


def action_new_tab(self) -> None:
    import asyncio as _asyncio

    def _on_picked(title: str | None) -> None:
        if not title:
            return
        layout = self._default_seed_layout()
        _asyncio.create_task(self.add_tab(title, layout, activate=True))

    self.push_screen(NewTabScreen(), _on_picked)


def action_close_active_tab(self) -> None:
    import asyncio as _asyncio
    if self._active_tab_id is None:
        return
    target = self._active_tab_id

    async def _go() -> None:
        result = await self.close_tab(target)
        if "error" in result:
            self.notify(f"can't close tab: {result['error']}", severity="warning")

    _asyncio.create_task(_go())
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_app_smoke_tabs.py -v -k "ctrl_t or ctrl_w"`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add patchfeld/widgets/new_tab_screen.py patchfeld/app.py tests/test_app_smoke_tabs.py
git commit -m "feat(app): ctrl+t new-tab modal + ctrl+w close-active-tab hotkeys"
```

---

## Task 18: End-to-end pilot test — orchestrator drives tabs

**Files:**
- Test: `tests/test_app_smoke_tabs.py`

This is the round-trip test that proves the user's motivating phrase works: the orchestrator can add a new tab containing a FileTree+FileViewer split.

- [ ] **Step 1: Write the test**

Append to `tests/test_app_smoke_tabs.py`:

```python
@pytest.mark.asyncio
async def test_orchestrator_can_add_a_filetree_filviewer_tab(tmp_path):
    """End-to-end: agent calls add_tab with an inline 2-panel layout, then
    set_layout on that tab, and the panels appear in the DOM."""
    app = _build_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        from patchfeld.orchestrator.tabs_tools import add_tab_handler
        add = add_tab_handler(app)
        result = await add({
            "title": "Code",
            "layout": {
                "version": 1,
                "layout": {
                    "type": "horizontal",
                    "children": [
                        {"id": "tree", "size": "30%",
                         "widget": "FileTree", "props": {"path": "."}},
                        {"id": "view", "size": "70%",
                         "widget": "FileViewer",
                         "props": {"follow_selection": True}},
                    ],
                },
            },
        })
        await pilot.pause()
        body = json.loads(result["content"][0]["text"])
        new_id = body["tab_id"]
        # Both panels mounted in the DOM.
        assert app.query_one("#panel-tree") is not None
        assert app.query_one("#panel-view") is not None
        # And the new tab is the active one.
        assert app._active_tab_id == new_id
```

- [ ] **Step 2: Run the test**

Run: `uv run pytest tests/test_app_smoke_tabs.py::test_orchestrator_can_add_a_filetree_filviewer_tab -v`
Expected: PASS (everything was wired in earlier tasks).

- [ ] **Step 3: Run the full suite**

Run: `uv run pytest tests/ -v`
Expected: all pass. Address any latent regression a previous task missed.

- [ ] **Step 4: Commit**

```bash
git add tests/test_app_smoke_tabs.py
git commit -m "test(app): end-to-end orchestrator-driven multi-panel tab creation"
```

---

## Self-review — spec coverage

| Spec section | Implemented in |
|---|---|
| Tabs node | Task 1 |
| Discriminated union for Container/Tabs/Panel | Task 1 |
| Relax LayoutSpec invariant (at most one chat) | Task 1 |
| Tab + Workspace models + invariants | Task 2 |
| `_contains_chat` helper | Task 2 |
| `workspace_from_layout` helper | Task 7 |
| `workspace_store.py` (load/save) | Task 3 |
| `project_workspace_path` | Task 3 |
| Engine `_build` for Tabs | Task 4 |
| Engine `_collect_panels` recurses into Tabs | Task 5 |
| TabAdded/TabClosed/TabSwitched events | Task 6 |
| `tab_id` on LayoutApplied/LayoutFailed | Task 6 |
| App composition with TabbedContent + per-tab containers | Task 7 |
| Up-front mount of all tabs | Task 7 (`_mount_workspace`) |
| Migration from layout.json | Task 8 (test) + Task 7 (code in `_load_or_seed_workspace`) |
| TabActivated handler — persist + event + focus | Task 9 |
| `add_tab` MCP tool | Task 10 |
| `close_tab` MCP tool with chat-invariant | Task 11 |
| `switch_tab` MCP tool | Task 12 |
| `list_tabs` MCP tool | Task 13 |
| Wire tab tools into MCP server | Task 14 |
| `set_layout`/`get_layout`/`save_layout`/`load_layout` tab-aware | Task 15 |
| `load_layout(as_new_tab=True)` | Task 15 |
| `ctrl+pgup/pgdn` (Textual default) + `ctrl+1..9` | Task 16 |
| `ctrl+t` modal + `ctrl+w` close | Task 17 |
| End-to-end orchestrator-driven tab creation | Task 18 |
| Pilot persistence test (Notebook survives switch) | Task 9 |

All spec requirements are covered.
