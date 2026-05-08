# Patchfeld — Walking Skeleton Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the foundational, testable skeleton of `patchfeld` — a Textual app that boots into the dashboard layout, with chrome wired up, a working LayoutEngine + WidgetRegistry + EventBus, persistence of the current layout and the orchestrator's transcript, and a fake echo "orchestrator session" so the user can type into the OrchestratorChat panel and see replies. **No real Claude Agent SDK yet.** This plan ends with a usable scaffold whose architecture has been validated end-to-end; subsequent plans drop in the real SDK and richer features.

**Architecture:** One Python process, one asyncio event loop. The Textual `App` mounts persistent chrome (`CommandBar`, `StatusBar`) and delegates the panel area to a `LayoutEngine` that diffs an in-memory `LayoutSpec` and mounts widgets from a `WidgetRegistry`. A synchronous in-process `EventBus` decouples widgets, the fake orchestrator session, and persistence. State lives at `<cwd>/.patchfeld/`.

**Tech Stack:** Python 3.11+, Textual, pydantic v2, pytest + pytest-asyncio, `uv` for dependency management.

**Non-goals for this plan (deferred to later plans):** real Claude Agent SDK, AgentManager, child agents, layout mutability via tools, config hot-reload, custom widgets (mode C), Terminal/PTY widget, save/load named layouts, History view.

---

## File Structure

```
patchfeld/
  __init__.py
  __main__.py                  # entry point: python -m patchfeld
  app.py                       # Textual App, chrome composition, key bindings
  events.py                    # EventBus + Event dataclasses
  layout/
    __init__.py
    spec.py                    # pydantic models: LayoutSpec, Container, Panel
    registry.py                # WidgetRegistry (curated lookup; exec deferred)
    engine.py                  # diff(old, new) -> Operations; apply(ops, root)
    defaults.py                # built-in dashboard LayoutSpec
  widgets/
    __init__.py
    chrome.py                  # CommandBar, StatusBar
    orchestrator_chat.py       # OrchestratorChat (messages list + input)
    placeholders.py            # AgentTable, ActivityFeed (empty placeholders)
  orchestrator/
    __init__.py
    fake_session.py            # echo "orchestrator" wired through EventBus
  persistence/
    __init__.py
    paths.py                   # per-cwd + global path helpers
    atomic.py                  # atomic JSON write helper
    layout_store.py            # read/write layout.json
    transcript_store.py        # append/read orchestrator.jsonl
tests/
  __init__.py
  test_events.py
  test_layout_spec.py
  test_atomic.py
  test_paths.py
  test_layout_store.py
  test_registry.py
  test_defaults.py
  test_layout_engine_diff.py
  test_app_smoke.py            # uses App.run_test()
  test_transcript_store.py
  test_fake_orchestrator.py
pyproject.toml
.gitignore
```

Each file has one responsibility. `events.py` is shared infrastructure but deliberately small. The `layout/` package owns spec + diff + registry; the `widgets/` package owns presentation; `orchestrator/` owns the placeholder session loop; `persistence/` owns disk I/O.

---

## Task 1: Project scaffolding & git init

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `patchfeld/__init__.py`
- Create: `patchfeld/__main__.py`
- Create: `tests/__init__.py`

- [ ] **Step 1: Initialize git**

```bash
cd /Users/jimmy.mills/Developer/patchfeld
git init
```

Expected: `Initialized empty Git repository in /Users/jimmy.mills/Developer/patchfeld/.git/`

- [ ] **Step 2: Create `.gitignore`**

```
__pycache__/
*.pyc
.venv/
.uv/
dist/
*.egg-info/
.pytest_cache/
.patchfeld/
.superpowers/
.DS_Store
```

- [ ] **Step 3: Create `pyproject.toml`**

```toml
[project]
name = "patchfeld"
version = "0.0.1"
description = "A Textual TUI for managing multiple Claude Code agent sessions"
requires-python = ">=3.11"
dependencies = [
  "textual>=0.80",
  "pydantic>=2.6",
]

[project.optional-dependencies]
dev = [
  "pytest>=8.0",
  "pytest-asyncio>=0.23",
]

[project.scripts]
patchfeld = "patchfeld.__main__:main"
mt = "patchfeld.__main__:main"

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["patchfeld"]
```

- [ ] **Step 4: Create `patchfeld/__init__.py`**

```python
__version__ = "0.0.1"
```

- [ ] **Step 5: Create `patchfeld/__main__.py`** (placeholder; real App wired in Task 15)

```python
def main() -> int:
    print("patchfeld scaffolding ready (no app yet)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 6: Create empty `tests/__init__.py`**

```python
```

- [ ] **Step 7: Install deps with uv**

```bash
uv venv
uv pip install -e ".[dev]"
```

Expected: virtualenv created, deps install cleanly. (If `uv` is missing, fall back to `python -m venv .venv && .venv/bin/pip install -e ".[dev]"`.)

- [ ] **Step 8: Smoke check**

```bash
.venv/bin/python -m patchfeld
```

Expected: `patchfeld scaffolding ready (no app yet)`

```bash
.venv/bin/pytest -q
```

Expected: `no tests ran` (zero failures).

- [ ] **Step 9: Commit**

```bash
git add pyproject.toml .gitignore patchfeld tests
git commit -m "chore: project scaffolding"
```

---

## Task 2: EventBus

**Files:**
- Create: `patchfeld/events.py`
- Test: `tests/test_events.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_events.py`:

```python
from dataclasses import dataclass

from patchfeld.events import EventBus


@dataclass
class Ping:
    msg: str


@dataclass
class Pong:
    n: int


def test_publish_with_no_subscribers_is_noop():
    bus = EventBus()
    bus.publish(Ping("hello"))  # must not raise


def test_subscriber_receives_published_event_of_matching_type():
    bus = EventBus()
    received: list[Ping] = []
    bus.subscribe(Ping, received.append)

    bus.publish(Ping("hi"))

    assert received == [Ping("hi")]


def test_subscriber_only_receives_events_of_subscribed_type():
    bus = EventBus()
    pings: list[Ping] = []
    pongs: list[Pong] = []
    bus.subscribe(Ping, pings.append)
    bus.subscribe(Pong, pongs.append)

    bus.publish(Ping("x"))
    bus.publish(Pong(3))

    assert pings == [Ping("x")]
    assert pongs == [Pong(3)]


def test_multiple_subscribers_each_receive_event():
    bus = EventBus()
    a, b = [], []
    bus.subscribe(Ping, a.append)
    bus.subscribe(Ping, b.append)

    bus.publish(Ping("y"))

    assert a == [Ping("y")] and b == [Ping("y")]


def test_unsubscribe_stops_delivery():
    bus = EventBus()
    received: list[Ping] = []
    unsub = bus.subscribe(Ping, received.append)

    bus.publish(Ping("first"))
    unsub()
    bus.publish(Ping("second"))

    assert received == [Ping("first")]


def test_handler_exception_does_not_break_other_handlers():
    bus = EventBus()
    good: list[Ping] = []

    def bad(_):
        raise RuntimeError("boom")

    bus.subscribe(Ping, bad)
    bus.subscribe(Ping, good.append)

    bus.publish(Ping("x"))  # must not raise

    assert good == [Ping("x")]
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
.venv/bin/pytest tests/test_events.py -v
```

Expected: ImportError on `patchfeld.events`.

- [ ] **Step 3: Implement `patchfeld/events.py`**

```python
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, TypeVar

log = logging.getLogger(__name__)

E = TypeVar("E")
Handler = Callable[[E], None]
Unsubscribe = Callable[[], None]


# --- Built-in event types (more added by later plans) ----------------------

@dataclass(frozen=True)
class UserMessageToOrchestrator:
    """User typed something into the orchestrator chat or command bar."""
    text: str


@dataclass(frozen=True)
class OrchestratorReply:
    """The orchestrator session emitted a reply."""
    text: str


@dataclass(frozen=True)
class StatsUpdated:
    """StatusBar stats refresh."""
    tokens_in: int = 0
    tokens_out: int = 0
    cost: float = 0.0
    active_agents: int = 0


# --- The bus ---------------------------------------------------------------

class EventBus:
    """Synchronous in-process pub/sub keyed by event class.

    Handlers are called in subscription order. Handler exceptions are logged
    and swallowed so one bad handler can't take down the others.
    """

    def __init__(self) -> None:
        self._subs: dict[type, list[Handler]] = {}

    def subscribe(self, event_type: type[E], handler: Handler) -> Unsubscribe:
        self._subs.setdefault(event_type, []).append(handler)

        def unsubscribe() -> None:
            handlers = self._subs.get(event_type)
            if handlers and handler in handlers:
                handlers.remove(handler)

        return unsubscribe

    def publish(self, event: object) -> None:
        for handler in list(self._subs.get(type(event), [])):
            try:
                handler(event)
            except Exception:
                log.exception("EventBus handler raised")
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
.venv/bin/pytest tests/test_events.py -v
```

Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add patchfeld/events.py tests/test_events.py
git commit -m "feat(events): add synchronous EventBus and core event types"
```

---

## Task 3: LayoutSpec models

**Files:**
- Create: `patchfeld/layout/__init__.py`
- Create: `patchfeld/layout/spec.py`
- Test: `tests/test_layout_spec.py`

- [ ] **Step 1: Create empty `patchfeld/layout/__init__.py`**

```python
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_layout_spec.py`:

```python
import pytest

from patchfeld.layout.spec import Container, LayoutSpec, Panel


def _minimal() -> dict:
    return {
        "version": 1,
        "layout": {"id": "orch", "widget": "OrchestratorChat"},
    }


def test_minimal_spec_parses():
    spec = LayoutSpec.model_validate(_minimal())
    assert isinstance(spec.layout, Panel)
    assert spec.layout.widget == "OrchestratorChat"
    assert spec.custom_widgets == []
    assert spec.focus is None


def test_nested_container_parses():
    spec = LayoutSpec.model_validate({
        "version": 1,
        "layout": {
            "type": "horizontal",
            "children": [
                {"id": "orch", "widget": "OrchestratorChat", "size": "60%"},
                {
                    "type": "vertical",
                    "size": "40%",
                    "children": [
                        {"id": "agents", "widget": "AgentTable"},
                        {"id": "feed", "widget": "ActivityFeed"},
                    ],
                },
            ],
        },
        "focus": "orch",
    })
    root = spec.layout
    assert isinstance(root, Container) and root.type == "horizontal"
    assert len(root.children) == 2
    assert isinstance(root.children[0], Panel)
    assert isinstance(root.children[1], Container)
    assert spec.focus == "orch"


def test_spec_without_orchestrator_chat_is_rejected():
    with pytest.raises(ValueError, match="OrchestratorChat"):
        LayoutSpec.model_validate({
            "version": 1,
            "layout": {"id": "x", "widget": "AgentTable"},
        })


def test_spec_with_two_orchestrator_chats_is_rejected():
    with pytest.raises(ValueError, match="exactly one"):
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


def test_panel_extra_fields_rejected():
    with pytest.raises(ValueError):
        LayoutSpec.model_validate({
            "version": 1,
            "layout": {"id": "orch", "widget": "OrchestratorChat", "bogus": True},
        })


def test_container_with_no_children_rejected():
    with pytest.raises(ValueError):
        LayoutSpec.model_validate({
            "version": 1,
            "layout": {"type": "horizontal", "children": []},
        })


def test_round_trip_json():
    src = LayoutSpec.model_validate(_minimal())
    dumped = src.model_dump_json()
    again = LayoutSpec.model_validate_json(dumped)
    assert again == src
```

- [ ] **Step 3: Run tests to confirm they fail**

```bash
.venv/bin/pytest tests/test_layout_spec.py -v
```

Expected: ImportError on `patchfeld.layout.spec`.

- [ ] **Step 4: Implement `patchfeld/layout/spec.py`**

```python
from __future__ import annotations

from typing import Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Panel(BaseModel):
    """A leaf node — one widget instance."""
    model_config = ConfigDict(extra="forbid")

    id: str
    widget: str
    props: dict = Field(default_factory=dict)
    size: str | None = None


class Container(BaseModel):
    """A non-leaf node — splits its area horizontally or vertically."""
    model_config = ConfigDict(extra="forbid")

    type: Literal["horizontal", "vertical"]
    size: str | None = None
    children: list["Node"] = Field(min_length=1)


Node = Union[Container, Panel]
Container.model_rebuild()


class CustomWidget(BaseModel):
    """A user/orchestrator-supplied Textual widget class. Mode C — wired in
    a later plan; the field exists now so the spec format is stable."""
    model_config = ConfigDict(extra="forbid")

    name: str
    source: str


class LayoutSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    layout: Node
    focus: str | None = None
    custom_widgets: list[CustomWidget] = Field(default_factory=list)

    @model_validator(mode="after")
    def _exactly_one_orchestrator_chat(self) -> "LayoutSpec":
        count = _count_orchestrator(self.layout)
        if count == 0:
            raise ValueError(
                "LayoutSpec must contain a panel with widget='OrchestratorChat'"
            )
        if count > 1:
            raise ValueError(
                "LayoutSpec must contain exactly one OrchestratorChat panel"
            )
        return self


def _count_orchestrator(node: Node) -> int:
    if isinstance(node, Panel):
        return 1 if node.widget == "OrchestratorChat" else 0
    return sum(_count_orchestrator(c) for c in node.children)
```

- [ ] **Step 5: Run tests to confirm they pass**

```bash
.venv/bin/pytest tests/test_layout_spec.py -v
```

Expected: 7 passed.

- [ ] **Step 6: Commit**

```bash
git add patchfeld/layout tests/test_layout_spec.py
git commit -m "feat(layout): pydantic LayoutSpec with OrchestratorChat invariant"
```

---

## Task 4: Atomic JSON write helper

**Files:**
- Create: `patchfeld/persistence/__init__.py`
- Create: `patchfeld/persistence/atomic.py`
- Test: `tests/test_atomic.py`

- [ ] **Step 1: Create empty `patchfeld/persistence/__init__.py`**

```python
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_atomic.py`:

```python
import json
from pathlib import Path

from patchfeld.persistence.atomic import write_json_atomic


def test_writes_file(tmp_path: Path):
    target = tmp_path / "out.json"
    write_json_atomic(target, {"a": 1, "b": [2, 3]})
    assert json.loads(target.read_text()) == {"a": 1, "b": [2, 3]}


def test_creates_parent_dirs(tmp_path: Path):
    target = tmp_path / "deep" / "nested" / "x.json"
    write_json_atomic(target, {"ok": True})
    assert target.exists()


def test_overwrites_existing_file(tmp_path: Path):
    target = tmp_path / "x.json"
    target.write_text('{"old": true}')
    write_json_atomic(target, {"new": True})
    assert json.loads(target.read_text()) == {"new": True}


def test_no_temp_file_left_after_success(tmp_path: Path):
    target = tmp_path / "x.json"
    write_json_atomic(target, {"a": 1})
    siblings = [p.name for p in tmp_path.iterdir()]
    assert siblings == ["x.json"]
```

- [ ] **Step 3: Run tests to confirm they fail**

```bash
.venv/bin/pytest tests/test_atomic.py -v
```

Expected: ImportError on `patchfeld.persistence.atomic`.

- [ ] **Step 4: Implement `patchfeld/persistence/atomic.py`**

```python
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def write_json_atomic(path: Path, data: Any) -> None:
    """Write JSON to `path` atomically: write to a temp file in the same
    directory, fsync, then rename. Same-directory rename is atomic on POSIX.
    Parent directories are created if missing."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp",
                                    dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise
```

- [ ] **Step 5: Run tests to confirm they pass**

```bash
.venv/bin/pytest tests/test_atomic.py -v
```

Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
git add patchfeld/persistence/__init__.py patchfeld/persistence/atomic.py tests/test_atomic.py
git commit -m "feat(persistence): atomic JSON write helper"
```

---

## Task 5: Path helpers

**Files:**
- Create: `patchfeld/persistence/paths.py`
- Test: `tests/test_paths.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_paths.py`:

```python
from pathlib import Path

from patchfeld.persistence.paths import (
    global_config_dir,
    project_state_dir,
    project_layout_path,
    project_transcript_path,
    project_orchestrator_transcript,
)


def test_project_state_dir_is_cwd_dot_patchfeld(tmp_path: Path):
    assert project_state_dir(tmp_path) == tmp_path / ".patchfeld"


def test_project_layout_path(tmp_path: Path):
    assert project_layout_path(tmp_path) == tmp_path / ".patchfeld" / "layout.json"


def test_project_transcript_path(tmp_path: Path):
    assert project_transcript_path(tmp_path, "abc123") == (
        tmp_path / ".patchfeld" / "transcripts" / "abc123.jsonl"
    )


def test_project_orchestrator_transcript(tmp_path: Path):
    assert project_orchestrator_transcript(tmp_path) == (
        tmp_path / ".patchfeld" / "transcripts" / "orchestrator.jsonl"
    )


def test_global_config_dir_under_xdg_or_home(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert global_config_dir() == tmp_path / "patchfeld"

    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert global_config_dir() == tmp_path / ".config" / "patchfeld"
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
.venv/bin/pytest tests/test_paths.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement `patchfeld/persistence/paths.py`**

```python
from __future__ import annotations

import os
from pathlib import Path


def project_state_dir(cwd: Path) -> Path:
    return Path(cwd) / ".patchfeld"


def project_layout_path(cwd: Path) -> Path:
    return project_state_dir(cwd) / "layout.json"


def project_transcripts_dir(cwd: Path) -> Path:
    return project_state_dir(cwd) / "transcripts"


def project_transcript_path(cwd: Path, agent_id: str) -> Path:
    return project_transcripts_dir(cwd) / f"{agent_id}.jsonl"


def project_orchestrator_transcript(cwd: Path) -> Path:
    return project_transcripts_dir(cwd) / "orchestrator.jsonl"


def global_config_dir() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "patchfeld"
    return Path(os.environ["HOME"]) / ".config" / "patchfeld"
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
.venv/bin/pytest tests/test_paths.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add patchfeld/persistence/paths.py tests/test_paths.py
git commit -m "feat(persistence): per-project and global path helpers"
```

---

## Task 6: LayoutStore (read/write layout.json)

**Files:**
- Create: `patchfeld/persistence/layout_store.py`
- Test: `tests/test_layout_store.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_layout_store.py`:

```python
from pathlib import Path

from patchfeld.layout.spec import LayoutSpec
from patchfeld.persistence.layout_store import load_layout, save_layout


def _spec() -> LayoutSpec:
    return LayoutSpec.model_validate({
        "version": 1,
        "layout": {"id": "orch", "widget": "OrchestratorChat"},
    })


def test_load_returns_none_when_no_file(tmp_path: Path):
    assert load_layout(tmp_path) is None


def test_save_then_load_round_trips(tmp_path: Path):
    save_layout(tmp_path, _spec())
    loaded = load_layout(tmp_path)
    assert loaded == _spec()


def test_save_creates_state_dir(tmp_path: Path):
    save_layout(tmp_path, _spec())
    assert (tmp_path / ".patchfeld" / "layout.json").exists()


def test_load_corrupted_file_returns_none(tmp_path: Path):
    state = tmp_path / ".patchfeld"
    state.mkdir()
    (state / "layout.json").write_text("not json {{")
    assert load_layout(tmp_path) is None


def test_load_invalid_spec_returns_none(tmp_path: Path):
    state = tmp_path / ".patchfeld"
    state.mkdir()
    (state / "layout.json").write_text(
        '{"version": 1, "layout": {"id": "x", "widget": "AgentTable"}}'
    )
    # Missing OrchestratorChat — invariant violated.
    assert load_layout(tmp_path) is None
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
.venv/bin/pytest tests/test_layout_store.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement `patchfeld/persistence/layout_store.py`**

```python
from __future__ import annotations

import json
import logging
from pathlib import Path

from patchfeld.layout.spec import LayoutSpec
from patchfeld.persistence.atomic import write_json_atomic
from patchfeld.persistence.paths import project_layout_path

log = logging.getLogger(__name__)


def save_layout(cwd: Path, spec: LayoutSpec) -> None:
    write_json_atomic(project_layout_path(cwd), spec.model_dump(mode="json"))


def load_layout(cwd: Path) -> LayoutSpec | None:
    path = project_layout_path(cwd)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text())
        return LayoutSpec.model_validate(raw)
    except Exception:
        log.exception("Failed to load layout from %s", path)
        return None
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
.venv/bin/pytest tests/test_layout_store.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add patchfeld/persistence/layout_store.py tests/test_layout_store.py
git commit -m "feat(persistence): layout.json read/write"
```

---

## Task 7: WidgetRegistry (curated lookup)

**Files:**
- Create: `patchfeld/layout/registry.py`
- Test: `tests/test_registry.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_registry.py`:

```python
import pytest
from textual.widget import Widget

from patchfeld.layout.registry import WidgetRegistry, UnknownWidgetError


class _W(Widget):
    pass


def test_register_then_get():
    reg = WidgetRegistry()
    reg.register("MyWidget", _W)
    assert reg.get("MyWidget") is _W


def test_get_unknown_raises():
    reg = WidgetRegistry()
    with pytest.raises(UnknownWidgetError):
        reg.get("Nope")


def test_double_register_replaces():
    reg = WidgetRegistry()

    class _A(Widget): ...
    class _B(Widget): ...

    reg.register("X", _A)
    reg.register("X", _B)
    assert reg.get("X") is _B


def test_known_returns_registered_names():
    reg = WidgetRegistry()
    reg.register("A", _W)
    reg.register("B", _W)
    assert set(reg.known()) == {"A", "B"}
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
.venv/bin/pytest tests/test_registry.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement `patchfeld/layout/registry.py`**

```python
from __future__ import annotations

from textual.widget import Widget


class UnknownWidgetError(KeyError):
    """Raised when a LayoutSpec references a widget name that is not registered."""


class WidgetRegistry:
    """Maps widget-type strings (as used in LayoutSpec) to Textual classes.

    Mode-C `register_custom_widget(name, source)` (which `exec`s code into an
    isolated namespace) is intentionally NOT implemented in this plan — it
    arrives in a later plan. This registry only supports curated registration
    of already-imported classes.
    """

    def __init__(self) -> None:
        self._classes: dict[str, type[Widget]] = {}

    def register(self, name: str, cls: type[Widget]) -> None:
        self._classes[name] = cls

    def get(self, name: str) -> type[Widget]:
        try:
            return self._classes[name]
        except KeyError as e:
            raise UnknownWidgetError(name) from e

    def known(self) -> list[str]:
        return list(self._classes.keys())
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
.venv/bin/pytest tests/test_registry.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add patchfeld/layout/registry.py tests/test_registry.py
git commit -m "feat(layout): WidgetRegistry for curated widget lookup"
```

---

## Task 8: Default landing layout

**Files:**
- Create: `patchfeld/layout/defaults.py`
- Test: `tests/test_defaults.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_defaults.py`:

```python
from patchfeld.layout.defaults import dashboard_layout
from patchfeld.layout.spec import Container, Panel


def test_dashboard_validates():
    spec = dashboard_layout()  # raises if invalid


def test_dashboard_has_three_panels_in_correct_arrangement():
    spec = dashboard_layout()
    root = spec.layout
    assert isinstance(root, Container) and root.type == "horizontal"
    assert len(root.children) == 2

    left = root.children[0]
    assert isinstance(left, Panel) and left.widget == "OrchestratorChat"
    assert left.id == "orch"

    right = root.children[1]
    assert isinstance(right, Container) and right.type == "vertical"
    assert len(right.children) == 2
    a, b = right.children
    assert isinstance(a, Panel) and a.widget == "AgentTable" and a.id == "agents"
    assert isinstance(b, Panel) and b.widget == "ActivityFeed" and b.id == "feed"


def test_dashboard_focus_is_orchestrator():
    assert dashboard_layout().focus == "orch"
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
.venv/bin/pytest tests/test_defaults.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement `patchfeld/layout/defaults.py`**

```python
from __future__ import annotations

from patchfeld.layout.spec import LayoutSpec


def dashboard_layout() -> LayoutSpec:
    """The built-in landing layout used when no <cwd>/.patchfeld/layout.json exists."""
    return LayoutSpec.model_validate({
        "version": 1,
        "layout": {
            "type": "horizontal",
            "children": [
                {"id": "orch", "size": "60%", "widget": "OrchestratorChat"},
                {
                    "type": "vertical",
                    "size": "40%",
                    "children": [
                        {"id": "agents", "size": "50%", "widget": "AgentTable"},
                        {"id": "feed", "size": "50%", "widget": "ActivityFeed"},
                    ],
                },
            ],
        },
        "focus": "orch",
    })
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
.venv/bin/pytest tests/test_defaults.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add patchfeld/layout/defaults.py tests/test_defaults.py
git commit -m "feat(layout): built-in dashboard landing layout"
```

---

## Task 9: Placeholder widgets (AgentTable, ActivityFeed)

These are intentionally empty in this plan. They become real in later plans when the AgentManager exists.

**Files:**
- Create: `patchfeld/widgets/__init__.py`
- Create: `patchfeld/widgets/placeholders.py`

- [ ] **Step 1: Create empty `patchfeld/widgets/__init__.py`**

```python
```

- [ ] **Step 2: Implement `patchfeld/widgets/placeholders.py`**

```python
from __future__ import annotations

from textual.containers import Container
from textual.widgets import Static


class AgentTable(Container):
    """Placeholder. Becomes a real DataTable wired to AgentManager in plan 2."""

    DEFAULT_CSS = """
    AgentTable {
        border: round $surface-lighten-2;
        padding: 0 1;
    }
    """

    def compose(self):
        yield Static("[dim]Agents — none yet[/dim]")


class ActivityFeed(Container):
    """Placeholder. Becomes a real event stream in plan 2."""

    DEFAULT_CSS = """
    ActivityFeed {
        border: round $surface-lighten-2;
        padding: 0 1;
    }
    """

    def compose(self):
        yield Static("[dim]Activity feed — empty[/dim]")
```

(No tests needed — these are exercised by the smoke test in Task 15.)

- [ ] **Step 3: Commit**

```bash
git add patchfeld/widgets/__init__.py patchfeld/widgets/placeholders.py
git commit -m "feat(widgets): empty AgentTable and ActivityFeed placeholders"
```

---

## Task 10: OrchestratorChat widget

A vertical container with a scrolling message list and an input box at the bottom. Submitting publishes `UserMessageToOrchestrator` to the EventBus. It subscribes to `OrchestratorReply` and appends to its message list.

**Files:**
- Create: `patchfeld/widgets/orchestrator_chat.py`

- [ ] **Step 1: Implement `patchfeld/widgets/orchestrator_chat.py`**

```python
from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Input, Static

from patchfeld.events import EventBus, OrchestratorReply, UserMessageToOrchestrator


class OrchestratorChat(Vertical):
    """Manager-Claude chat panel: scrolling message list + input box."""

    DEFAULT_CSS = """
    OrchestratorChat {
        border: round $primary;
        padding: 0 1;
    }
    OrchestratorChat #orch-messages {
        height: 1fr;
    }
    OrchestratorChat #orch-input {
        dock: bottom;
        height: 3;
    }
    OrchestratorChat .msg-user {
        color: $accent;
    }
    OrchestratorChat .msg-orch {
        color: $text;
    }
    """

    def __init__(self, *, event_bus: EventBus | None = None,
                 history: list[tuple[str, str]] | None = None) -> None:
        """history: optional list of (role, text) preloaded messages."""
        super().__init__()
        self._bus = event_bus
        self._history = history or []
        self._unsub = lambda: None

    def compose(self) -> ComposeResult:
        yield VerticalScroll(id="orch-messages")
        yield Input(placeholder="Message orchestrator… (enter to send)",
                    id="orch-input")

    def on_mount(self) -> None:
        bus = self._bus or getattr(self.app, "event_bus", None)
        if bus is None:
            return
        for role, text in self._history:
            self._append_line(role, text)
        self._unsub = bus.subscribe(
            OrchestratorReply, lambda e: self._append_line("orch", e.text)
        )

    def on_unmount(self) -> None:
        self._unsub()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if not event.value.strip():
            return
        text = event.value
        bus = self._bus or getattr(self.app, "event_bus", None)
        self._append_line("user", text)
        event.input.value = ""
        if bus is not None:
            bus.publish(UserMessageToOrchestrator(text))

    def _append_line(self, role: str, text: str) -> None:
        msgs = self.query_one("#orch-messages", VerticalScroll)
        prefix = "you" if role == "user" else "claude"
        cls = "msg-user" if role == "user" else "msg-orch"
        msgs.mount(Static(f"[{cls}]{prefix}:[/{cls}] {text}"))
        msgs.scroll_end(animate=False)
```

- [ ] **Step 2: Commit**

```bash
git add patchfeld/widgets/orchestrator_chat.py
git commit -m "feat(widgets): OrchestratorChat panel with input + EventBus wiring"
```

(Behavior is covered by the smoke test in Task 15 plus the fake-orchestrator test in Task 19.)

---

## Task 11: CommandBar (chrome)

**Files:**
- Create: `patchfeld/widgets/chrome.py` (CommandBar; StatusBar appended in Task 12)

- [ ] **Step 1: Implement initial `patchfeld/widgets/chrome.py`**

```python
from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Input, Static

from patchfeld.events import EventBus, UserMessageToOrchestrator


class CommandBar(Horizontal):
    """Top bar — `/` focuses; submitting sends to the orchestrator."""

    DEFAULT_CSS = """
    CommandBar {
        height: 1;
        background: $surface-darken-1;
    }
    CommandBar Input {
        border: none;
        padding: 0;
        background: $surface-darken-1;
    }
    CommandBar Static {
        width: 7;
        color: $text-muted;
    }
    """

    def __init__(self, *, event_bus: EventBus | None = None) -> None:
        super().__init__()
        self._bus = event_bus

    def compose(self) -> ComposeResult:
        yield Static("mt :> ")
        yield Input(placeholder="message orchestrator", id="cmd-input")

    def focus_input(self) -> None:
        self.query_one("#cmd-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if not event.value.strip():
            return
        bus = self._bus or getattr(self.app, "event_bus", None)
        if bus is not None:
            bus.publish(UserMessageToOrchestrator(event.value))
        event.input.value = ""
```

- [ ] **Step 2: Commit**

```bash
git add patchfeld/widgets/chrome.py
git commit -m "feat(widgets): CommandBar chrome"
```

---

## Task 12: StatusBar (chrome)

**Files:**
- Modify: `patchfeld/widgets/chrome.py` (append StatusBar)

- [ ] **Step 1: Append `StatusBar` to `patchfeld/widgets/chrome.py`**

Add to the bottom of the file:

```python
class StatusBar(Horizontal):
    """Bottom bar: tokens / cost / active agents / current layout name / [E]."""

    DEFAULT_CSS = """
    StatusBar {
        height: 1;
        background: $surface-darken-1;
    }
    StatusBar Static {
        padding: 0 1;
    }
    """

    def __init__(self, *, event_bus: EventBus | None = None,
                 layout_name: str = "default") -> None:
        super().__init__()
        self._bus = event_bus
        self._layout_name = layout_name
        self._unsub = lambda: None

    def compose(self) -> ComposeResult:
        yield Static("tokens 0/0", id="sb-tokens")
        yield Static("$0.00", id="sb-cost")
        yield Static("0 agents", id="sb-agents")
        yield Static(f"layout: {self._layout_name}", id="sb-layout")
        yield Static("", id="sb-error")

    def on_mount(self) -> None:
        from patchfeld.events import StatsUpdated
        bus = self._bus or getattr(self.app, "event_bus", None)
        if bus is None:
            return
        self._unsub = bus.subscribe(StatsUpdated, self._on_stats)

    def on_unmount(self) -> None:
        self._unsub()

    def _on_stats(self, event) -> None:
        self.query_one("#sb-tokens", Static).update(
            f"tokens {event.tokens_in}/{event.tokens_out}"
        )
        self.query_one("#sb-cost", Static).update(f"${event.cost:.2f}")
        self.query_one("#sb-agents", Static).update(f"{event.active_agents} agents")

    def set_layout_name(self, name: str) -> None:
        self._layout_name = name
        self.query_one("#sb-layout", Static).update(f"layout: {name}")

    def set_error(self, msg: str | None) -> None:
        self.query_one("#sb-error", Static).update("[E]" if msg else "")
```

- [ ] **Step 2: Commit**

```bash
git add patchfeld/widgets/chrome.py
git commit -m "feat(widgets): StatusBar chrome"
```

---

## Task 13: LayoutEngine.diff (pure function)

The diff is identity-keyed: panels with the same `id` and same `widget` are reused; same `id` with different `widget` is a swap; missing ids are unmounted; new ids are mounted. We represent the result as a flat list of operations the apply step consumes.

**Files:**
- Create: `patchfeld/layout/engine.py` (diff only; apply added in Task 14)
- Test: `tests/test_layout_engine_diff.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_layout_engine_diff.py`:

```python
from patchfeld.layout.defaults import dashboard_layout
from patchfeld.layout.engine import (
    MountPanel,
    UnmountPanel,
    UpdateProps,
    diff,
)
from patchfeld.layout.spec import LayoutSpec


def _spec(panels: list[dict]) -> LayoutSpec:
    return LayoutSpec.model_validate({
        "version": 1,
        "layout": {"type": "horizontal", "children": panels},
    })


def test_initial_diff_mounts_everything():
    new = dashboard_layout()
    ops = diff(None, new)
    mounts = [op for op in ops if isinstance(op, MountPanel)]
    assert {op.panel.id for op in mounts} == {"orch", "agents", "feed"}


def test_no_change_produces_no_ops():
    spec = dashboard_layout()
    assert diff(spec, spec) == []


def test_changed_props_produces_update():
    a = _spec([
        {"id": "orch", "widget": "OrchestratorChat", "props": {"x": 1}},
    ])
    b = _spec([
        {"id": "orch", "widget": "OrchestratorChat", "props": {"x": 2}},
    ])
    ops = diff(a, b)
    assert ops == [UpdateProps(panel_id="orch", props={"x": 2})]


def test_changed_widget_type_unmounts_then_mounts():
    a = _spec([
        {"id": "orch", "widget": "OrchestratorChat"},
        {"id": "x", "widget": "AgentTable"},
    ])
    b = _spec([
        {"id": "orch", "widget": "OrchestratorChat"},
        {"id": "x", "widget": "ActivityFeed"},
    ])
    ops = diff(a, b)
    kinds = [type(op).__name__ for op in ops]
    assert "UnmountPanel" in kinds and "MountPanel" in kinds


def test_removed_panel_is_unmounted():
    a = _spec([
        {"id": "orch", "widget": "OrchestratorChat"},
        {"id": "x", "widget": "AgentTable"},
    ])
    b = _spec([{"id": "orch", "widget": "OrchestratorChat"}])
    ops = diff(a, b)
    assert any(isinstance(op, UnmountPanel) and op.panel_id == "x" for op in ops)


def test_added_panel_is_mounted():
    a = _spec([{"id": "orch", "widget": "OrchestratorChat"}])
    b = _spec([
        {"id": "orch", "widget": "OrchestratorChat"},
        {"id": "agents", "widget": "AgentTable"},
    ])
    ops = diff(a, b)
    assert any(isinstance(op, MountPanel) and op.panel.id == "agents" for op in ops)
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
.venv/bin/pytest tests/test_layout_engine_diff.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement diff in `patchfeld/layout/engine.py`**

```python
from __future__ import annotations

from dataclasses import dataclass

from patchfeld.layout.spec import Container, LayoutSpec, Panel


# --- Operations -------------------------------------------------------------

@dataclass(frozen=True)
class MountPanel:
    panel: Panel


@dataclass(frozen=True)
class UnmountPanel:
    panel_id: str


@dataclass(frozen=True)
class UpdateProps:
    panel_id: str
    props: dict


Operation = MountPanel | UnmountPanel | UpdateProps


# --- Diff -------------------------------------------------------------------

def _collect_panels(node, out: dict[str, Panel]) -> None:
    if isinstance(node, Panel):
        out[node.id] = node
    elif isinstance(node, Container):
        for c in node.children:
            _collect_panels(c, out)


def diff(old: LayoutSpec | None, new: LayoutSpec) -> list[Operation]:
    """Compute the minimal set of mount/unmount/update operations to take the
    rendered widget tree from `old` to `new`.

    Note: this plan reuses widgets only when the panel id AND widget type are
    unchanged. Container restructuring is handled by Task 14's apply step,
    which rebuilds the container scaffolding from `new.layout` each call.
    Reusing identical panels means no scroll-jump or focus-loss for the cases
    that matter most (props-only changes)."""

    old_panels: dict[str, Panel] = {}
    new_panels: dict[str, Panel] = {}
    if old is not None:
        _collect_panels(old.layout, old_panels)
    _collect_panels(new.layout, new_panels)

    ops: list[Operation] = []

    for pid, op in old_panels.items():
        if pid not in new_panels or new_panels[pid].widget != op.widget:
            ops.append(UnmountPanel(panel_id=pid))

    for pid, np in new_panels.items():
        if pid not in old_panels or old_panels[pid].widget != np.widget:
            ops.append(MountPanel(panel=np))
            continue
        if old_panels[pid].props != np.props:
            ops.append(UpdateProps(panel_id=pid, props=np.props))

    return ops
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
.venv/bin/pytest tests/test_layout_engine_diff.py -v
```

Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add patchfeld/layout/engine.py tests/test_layout_engine_diff.py
git commit -m "feat(layout): identity-keyed diff(old, new) → operations"
```

---

## Task 14: LayoutEngine.apply (mount widgets into a Textual container)

The `apply` function takes a target container, a `LayoutSpec`, and a `WidgetRegistry`, and rebuilds the container's children to match the spec. Container scaffolding (Horizontal/Vertical splits) is rebuilt fresh each call; panels are instantiated by looking up `panel.widget` in the registry and passing `panel.props` as kwargs.

**Files:**
- Modify: `patchfeld/layout/engine.py` (append `apply`)

- [ ] **Step 1: Append `apply` to `patchfeld/layout/engine.py`**

```python
# --- Apply ------------------------------------------------------------------

from textual.containers import Container as TxContainer
from textual.containers import Horizontal, Vertical


def _build(node, registry) -> "TxContainer":
    if isinstance(node, Panel):
        cls = registry.get(node.widget)
        widget = cls(**node.props) if node.props else cls()
        widget.id = f"panel-{node.id}"
        if node.size:
            widget.styles.width = node.size if "%" in node.size or node.size.endswith("fr") else None
            widget.styles.height = None
        return widget
    box_cls = Horizontal if node.type == "horizontal" else Vertical
    box = box_cls(*[_build(c, registry) for c in node.children])
    if node.size:
        box.styles.width = node.size
    return box


async def apply(container: TxContainer, spec: LayoutSpec, registry) -> None:
    """Replace `container`'s children with widgets built from `spec.layout`.

    Atomic: builds the new tree fully (including registry lookups for every
    panel widget class) before touching the container. If any lookup raises
    UnknownWidgetError or instantiation throws, nothing is mounted.

    `focus` is honored after mount.

    Note for plan 1: `apply` rebuilds from scratch on every call. The `diff`
    function above is fully implemented and tested because its semantics are
    the stable contract — but `apply` only ever runs once per app lifetime in
    this plan (mounting the default dashboard at boot), so a diff-driven
    incremental application would be premature. Plan 4 (when `set_layout`
    becomes a runtime tool the orchestrator can call repeatedly) will switch
    `apply` to consume `diff()` operations and reuse mounted widgets where ids
    and widget types match.
    """
    new_children = [_build(spec.layout, registry)]

    await container.remove_children()
    await container.mount_all(new_children)

    if spec.focus:
        try:
            container.query_one(f"#panel-{spec.focus}").focus()
        except Exception:
            pass
```

- [ ] **Step 2: Commit**

```bash
git add patchfeld/layout/engine.py
git commit -m "feat(layout): apply(spec) mounts widgets atomically into a container"
```

(Tested via the smoke test in Task 15.)

---

## Task 15: Textual App shell + smoke test

**Files:**
- Create: `patchfeld/app.py`
- Test: `tests/test_app_smoke.py`
- Modify: `patchfeld/__main__.py`

- [ ] **Step 1: Implement `patchfeld/app.py`**

```python
from __future__ import annotations

from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Vertical

from patchfeld.events import EventBus
from patchfeld.layout.defaults import dashboard_layout
from patchfeld.layout.engine import apply as apply_layout
from patchfeld.layout.registry import WidgetRegistry
from patchfeld.layout.spec import LayoutSpec
from patchfeld.persistence.layout_store import load_layout, save_layout
from patchfeld.widgets.chrome import CommandBar, StatusBar
from patchfeld.widgets.orchestrator_chat import OrchestratorChat
from patchfeld.widgets.placeholders import ActivityFeed, AgentTable


def build_default_registry() -> WidgetRegistry:
    reg = WidgetRegistry()
    reg.register("OrchestratorChat", OrchestratorChat)
    reg.register("AgentTable", AgentTable)
    reg.register("ActivityFeed", ActivityFeed)
    return reg


class PatchfeldApp(App):
    """Walking-skeleton App. Real Claude Agent SDK wiring lives in plan 2."""

    CSS = """
    #panel-area {
        height: 1fr;
    }
    """

    # Tab/shift-tab cycle focus via Textual's built-in focus chain — no
    # explicit binding needed. ctrl-h (history) and ctrl-l (layout switcher)
    # ship in plan 4 alongside the features they open.
    BINDINGS = [
        Binding("/", "focus_command_bar", "command bar"),
        Binding("ctrl+q", "quit", "quit"),
        Binding("?", "show_help", "help"),
    ]

    def __init__(self, *, cwd: Path | None = None,
                 registry: WidgetRegistry | None = None) -> None:
        super().__init__()
        self.cwd = Path(cwd) if cwd else Path.cwd()
        self.event_bus = EventBus()
        self.registry = registry or build_default_registry()
        self._current_spec: LayoutSpec | None = None

    def compose(self) -> ComposeResult:
        yield CommandBar(event_bus=self.event_bus)
        yield Container(id="panel-area")
        yield StatusBar(event_bus=self.event_bus)

    async def on_mount(self) -> None:
        spec = load_layout(self.cwd) or dashboard_layout()
        await self._apply(spec)

    async def _apply(self, spec: LayoutSpec) -> None:
        area = self.query_one("#panel-area", Container)
        await apply_layout(area, spec, self.registry)
        self._current_spec = spec
        save_layout(self.cwd, spec)

    def action_focus_command_bar(self) -> None:
        self.query_one(CommandBar).focus_input()

    def action_show_help(self) -> None:
        self.notify(
            "/ command bar · ctrl-q quit · ? help",
            title="keybindings",
        )
```

- [ ] **Step 2: Update `patchfeld/__main__.py`**

```python
from patchfeld.app import PatchfeldApp


def main() -> int:
    PatchfeldApp().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 3: Write the smoke test**

Create `tests/test_app_smoke.py`:

```python
from pathlib import Path

import pytest

from patchfeld.app import PatchfeldApp
from patchfeld.widgets.chrome import CommandBar, StatusBar
from patchfeld.widgets.orchestrator_chat import OrchestratorChat
from patchfeld.widgets.placeholders import ActivityFeed, AgentTable


@pytest.mark.asyncio
async def test_default_dashboard_mounts_three_panels(tmp_path: Path):
    app = PatchfeldApp(cwd=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.query_one(CommandBar) is not None
        assert app.query_one(StatusBar) is not None
        assert app.query_one(OrchestratorChat) is not None
        assert app.query_one(AgentTable) is not None
        assert app.query_one(ActivityFeed) is not None


@pytest.mark.asyncio
async def test_slash_focuses_command_bar(tmp_path: Path):
    app = PatchfeldApp(cwd=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("/")
        cmd = app.query_one(CommandBar)
        assert cmd.query_one("#cmd-input").has_focus
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest tests/test_app_smoke.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add patchfeld/app.py patchfeld/__main__.py tests/test_app_smoke.py
git commit -m "feat(app): App shell with chrome + dashboard mount"
```

---

## Task 16: Persist layout on launch / restore on relaunch

`save_layout` is already called in `_apply`. Verify that re-launching in the same cwd restores the saved layout.

**Files:**
- Modify: `tests/test_app_smoke.py` (append a restore test)

- [ ] **Step 1: Append the restore test to `tests/test_app_smoke.py`**

```python
@pytest.mark.asyncio
async def test_layout_persists_across_app_runs(tmp_path: Path):
    # First run: launch, mount default, save.
    app1 = PatchfeldApp(cwd=tmp_path)
    async with app1.run_test() as pilot:
        await pilot.pause()
        assert (tmp_path / ".patchfeld" / "layout.json").exists()

    # Second run in same cwd: should load the saved layout.
    app2 = PatchfeldApp(cwd=tmp_path)
    async with app2.run_test() as pilot:
        await pilot.pause()
        assert app2._current_spec is not None
        # Default dashboard has the orch panel — it must still be there.
        assert app2.query_one(OrchestratorChat) is not None
```

- [ ] **Step 2: Run tests**

```bash
.venv/bin/pytest tests/test_app_smoke.py -v
```

Expected: 3 passed.

- [ ] **Step 3: Commit**

```bash
git add tests/test_app_smoke.py
git commit -m "test(app): layout persists across app runs"
```

---

## Task 17: Transcript store (jsonl)

Append-only message log per agent id. The orchestrator gets its own transcript at `transcripts/orchestrator.jsonl`.

**Files:**
- Create: `patchfeld/persistence/transcript_store.py`
- Test: `tests/test_transcript_store.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_transcript_store.py`:

```python
from pathlib import Path

from patchfeld.persistence.transcript_store import (
    OrchestratorTranscript,
    TranscriptEntry,
)


def test_append_and_read_round_trip(tmp_path: Path):
    store = OrchestratorTranscript(cwd=tmp_path)
    store.append(TranscriptEntry(role="user", text="hello"))
    store.append(TranscriptEntry(role="orch", text="hi back"))

    entries = store.read_all()
    assert entries == [
        TranscriptEntry(role="user", text="hello"),
        TranscriptEntry(role="orch", text="hi back"),
    ]


def test_read_all_when_empty_returns_empty_list(tmp_path: Path):
    store = OrchestratorTranscript(cwd=tmp_path)
    assert store.read_all() == []


def test_append_creates_transcripts_dir(tmp_path: Path):
    store = OrchestratorTranscript(cwd=tmp_path)
    store.append(TranscriptEntry(role="user", text="x"))
    assert (tmp_path / ".patchfeld" / "transcripts" / "orchestrator.jsonl").exists()


def test_corrupted_line_is_skipped(tmp_path: Path):
    store = OrchestratorTranscript(cwd=tmp_path)
    store.append(TranscriptEntry(role="user", text="ok"))

    target = tmp_path / ".patchfeld" / "transcripts" / "orchestrator.jsonl"
    with target.open("a") as f:
        f.write("not json\n")
    store.append(TranscriptEntry(role="orch", text="still works"))

    entries = store.read_all()
    assert entries == [
        TranscriptEntry(role="user", text="ok"),
        TranscriptEntry(role="orch", text="still works"),
    ]
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
.venv/bin/pytest tests/test_transcript_store.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement `patchfeld/persistence/transcript_store.py`**

```python
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path

from patchfeld.persistence.paths import (
    project_orchestrator_transcript,
    project_transcripts_dir,
)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class TranscriptEntry:
    role: str  # "user" | "orch"
    text: str


class OrchestratorTranscript:
    def __init__(self, cwd: Path) -> None:
        self._path = project_orchestrator_transcript(cwd)
        self._cwd = cwd

    def append(self, entry: TranscriptEntry) -> None:
        project_transcripts_dir(self._cwd).mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(entry)) + "\n")

    def read_all(self) -> list[TranscriptEntry]:
        if not self._path.exists():
            return []
        out: list[TranscriptEntry] = []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                out.append(TranscriptEntry(**json.loads(line)))
            except Exception:
                log.warning("Skipping corrupted transcript line: %r", line)
        return out
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
.venv/bin/pytest tests/test_transcript_store.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add patchfeld/persistence/transcript_store.py tests/test_transcript_store.py
git commit -m "feat(persistence): orchestrator transcript jsonl store"
```

---

## Task 18: Fake orchestrator session

Subscribes to `UserMessageToOrchestrator`, replies with `OrchestratorReply("I heard: <text>")`, and logs both sides to the transcript store.

**Files:**
- Create: `patchfeld/orchestrator/__init__.py`
- Create: `patchfeld/orchestrator/fake_session.py`
- Test: `tests/test_fake_orchestrator.py`

- [ ] **Step 1: Create empty `patchfeld/orchestrator/__init__.py`**

```python
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_fake_orchestrator.py`:

```python
from pathlib import Path

from patchfeld.events import EventBus, OrchestratorReply, UserMessageToOrchestrator
from patchfeld.orchestrator.fake_session import FakeOrchestratorSession
from patchfeld.persistence.transcript_store import (
    OrchestratorTranscript,
    TranscriptEntry,
)


def test_fake_session_echoes_user_input():
    bus = EventBus()
    received: list[OrchestratorReply] = []
    bus.subscribe(OrchestratorReply, received.append)

    session = FakeOrchestratorSession(bus=bus, transcript=None)
    session.start()

    bus.publish(UserMessageToOrchestrator("hello"))

    assert received == [OrchestratorReply("I heard: hello")]


def test_fake_session_writes_to_transcript(tmp_path: Path):
    bus = EventBus()
    transcript = OrchestratorTranscript(cwd=tmp_path)

    session = FakeOrchestratorSession(bus=bus, transcript=transcript)
    session.start()

    bus.publish(UserMessageToOrchestrator("ping"))

    assert transcript.read_all() == [
        TranscriptEntry(role="user", text="ping"),
        TranscriptEntry(role="orch", text="I heard: ping"),
    ]


def test_stop_unsubscribes():
    bus = EventBus()
    received: list[OrchestratorReply] = []
    bus.subscribe(OrchestratorReply, received.append)

    session = FakeOrchestratorSession(bus=bus, transcript=None)
    session.start()
    session.stop()

    bus.publish(UserMessageToOrchestrator("hi"))

    assert received == []
```

- [ ] **Step 3: Run tests to confirm they fail**

```bash
.venv/bin/pytest tests/test_fake_orchestrator.py -v
```

Expected: ImportError.

- [ ] **Step 4: Implement `patchfeld/orchestrator/fake_session.py`**

```python
from __future__ import annotations

from patchfeld.events import EventBus, OrchestratorReply, UserMessageToOrchestrator
from patchfeld.persistence.transcript_store import (
    OrchestratorTranscript,
    TranscriptEntry,
)


class FakeOrchestratorSession:
    """Stand-in for the real Claude Agent SDK orchestrator (wired in plan 2).

    Echoes user input back as 'I heard: <text>'. Writes both sides to the
    transcript store so we can verify persistence end-to-end before the real
    SDK is involved.
    """

    def __init__(
        self,
        *,
        bus: EventBus,
        transcript: OrchestratorTranscript | None,
    ) -> None:
        self._bus = bus
        self._transcript = transcript
        self._unsub = lambda: None

    def start(self) -> None:
        self._unsub = self._bus.subscribe(UserMessageToOrchestrator, self._handle)

    def stop(self) -> None:
        self._unsub()

    def _handle(self, event: UserMessageToOrchestrator) -> None:
        if self._transcript is not None:
            self._transcript.append(TranscriptEntry(role="user", text=event.text))
        reply = f"I heard: {event.text}"
        self._bus.publish(OrchestratorReply(reply))
        if self._transcript is not None:
            self._transcript.append(TranscriptEntry(role="orch", text=reply))
```

- [ ] **Step 5: Run tests to confirm they pass**

```bash
.venv/bin/pytest tests/test_fake_orchestrator.py -v
```

Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add patchfeld/orchestrator/__init__.py patchfeld/orchestrator/fake_session.py tests/test_fake_orchestrator.py
git commit -m "feat(orchestrator): fake echoing session for plan-1 walking skeleton"
```

---

## Task 19: Wire fake session into the App + restore transcript on launch

The `OrchestratorChat` widget needs the prior transcript to pre-populate, and the `FakeOrchestratorSession` needs to be running. Both happen in the App's `on_mount`.

**Files:**
- Modify: `patchfeld/app.py`
- Modify: `patchfeld/widgets/orchestrator_chat.py` (already supports `history=`; verify use)
- Modify: `tests/test_app_smoke.py` (append integration test)

- [ ] **Step 1: Modify `patchfeld/app.py`** — add fake session + transcript wiring

Replace the existing `PatchfeldApp` class with:

```python
class PatchfeldApp(App):
    """Walking-skeleton App. Real Claude Agent SDK wiring lives in plan 2."""

    CSS = """
    #panel-area {
        height: 1fr;
    }
    """

    # Tab/shift-tab cycle focus via Textual's built-in focus chain — no
    # explicit binding needed. ctrl-h (history) and ctrl-l (layout switcher)
    # ship in plan 4 alongside the features they open.
    BINDINGS = [
        Binding("/", "focus_command_bar", "command bar"),
        Binding("ctrl+q", "quit", "quit"),
        Binding("?", "show_help", "help"),
    ]

    def __init__(self, *, cwd: Path | None = None,
                 registry: WidgetRegistry | None = None) -> None:
        super().__init__()
        self.cwd = Path(cwd) if cwd else Path.cwd()
        self.event_bus = EventBus()
        self.registry = registry or build_default_registry()
        self._current_spec: LayoutSpec | None = None

        from patchfeld.orchestrator.fake_session import FakeOrchestratorSession
        from patchfeld.persistence.transcript_store import OrchestratorTranscript
        self.transcript = OrchestratorTranscript(cwd=self.cwd)
        self.session = FakeOrchestratorSession(
            bus=self.event_bus, transcript=self.transcript
        )
        # Make prior history available to the OrchestratorChat widget at mount.
        self.orchestrator_history: list[tuple[str, str]] = [
            (e.role, e.text) for e in self.transcript.read_all()
        ]

    def compose(self) -> ComposeResult:
        yield CommandBar(event_bus=self.event_bus)
        yield Container(id="panel-area")
        yield StatusBar(event_bus=self.event_bus)

    async def on_mount(self) -> None:
        self.session.start()
        spec = load_layout(self.cwd) or dashboard_layout()
        await self._apply(spec)

    async def _apply(self, spec: LayoutSpec) -> None:
        area = self.query_one("#panel-area", Container)
        await apply_layout(area, spec, self.registry)
        self._current_spec = spec
        save_layout(self.cwd, spec)

    def on_unmount(self) -> None:
        self.session.stop()

    def action_focus_command_bar(self) -> None:
        self.query_one(CommandBar).focus_input()

    def action_show_help(self) -> None:
        self.notify(
            "/ command bar · ctrl-q quit · ? help",
            title="keybindings",
        )
```

- [ ] **Step 2: Modify `build_default_registry()` to bake history into OrchestratorChat**

The cleanest path is for the OrchestratorChat widget to pull history off the App at mount time. Update its `on_mount` in `patchfeld/widgets/orchestrator_chat.py`:

Replace its `on_mount` method with:

```python
    def on_mount(self) -> None:
        bus = self._bus or getattr(self.app, "event_bus", None)
        if bus is None:
            return
        history = self._history or list(getattr(self.app, "orchestrator_history", []))
        for role, text in history:
            self._append_line(role, text)
        self._unsub = bus.subscribe(
            OrchestratorReply, lambda e: self._append_line("orch", e.text)
        )
```

- [ ] **Step 3: Append integration test to `tests/test_app_smoke.py`**

```python
@pytest.mark.asyncio
async def test_command_bar_message_round_trips_through_fake_orchestrator(tmp_path: Path):
    app = PatchfeldApp(cwd=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("/")
        await pilot.press(*"hello world")
        await pilot.press("enter")
        await pilot.pause()

        # Both sides land in the orchestrator's transcript on disk.
        from patchfeld.persistence.transcript_store import OrchestratorTranscript
        entries = OrchestratorTranscript(cwd=tmp_path).read_all()
        roles = [e.role for e in entries]
        texts = [e.text for e in entries]
        assert roles == ["user", "orch"]
        assert texts == ["hello world", "I heard: hello world"]


@pytest.mark.asyncio
async def test_transcript_restored_on_relaunch(tmp_path: Path):
    # First run: send one message.
    app1 = PatchfeldApp(cwd=tmp_path)
    async with app1.run_test() as pilot:
        await pilot.pause()
        await pilot.press("/")
        await pilot.press(*"persisted")
        await pilot.press("enter")
        await pilot.pause()

    # Second run: history should be loaded into the orchestrator.
    app2 = PatchfeldApp(cwd=tmp_path)
    async with app2.run_test() as pilot:
        await pilot.pause()
        assert app2.orchestrator_history == [
            ("user", "persisted"),
            ("orch", "I heard: persisted"),
        ]
```

- [ ] **Step 4: Run all tests**

```bash
.venv/bin/pytest -v
```

Expected: every test passes.

- [ ] **Step 5: Commit**

```bash
git add patchfeld/app.py patchfeld/widgets/orchestrator_chat.py tests/test_app_smoke.py
git commit -m "feat(app): wire fake orchestrator session and transcript restore"
```

---

## Task 20: Manual smoke test + final commit

- [ ] **Step 1: Launch the app**

```bash
.venv/bin/python -m patchfeld
```

Expected: terminal shows the dashboard layout — top command bar, OrchestratorChat on the left (60%), AgentTable + ActivityFeed stacked on the right (40%), status bar at bottom.

- [ ] **Step 2: Exercise it manually**

  1. Type a message in the OrchestratorChat input → press enter. Expected: message appears as "you: ..." and immediately "claude: I heard: ...".
  2. Press `/`. Expected: focus jumps to the top command bar.
  3. Type a message there → enter. Expected: the orchestrator chat appends both messages.
  4. Press `?`. Expected: a notification toast describes the keybindings.
  5. Press `ctrl-q`. Expected: app exits cleanly.

- [ ] **Step 3: Verify on-disk artifacts**

```bash
ls -la .patchfeld .patchfeld/transcripts
cat .patchfeld/layout.json
cat .patchfeld/transcripts/orchestrator.jsonl
```

Expected: `layout.json` contains the dashboard spec; `orchestrator.jsonl` has one JSON line per message you sent.

- [ ] **Step 4: Re-launch and confirm restore**

```bash
.venv/bin/python -m patchfeld
```

Expected: previous messages are visible in the OrchestratorChat panel before you type anything new.

- [ ] **Step 5: Final test sweep + commit**

```bash
.venv/bin/pytest -v
```

Expected: all green.

```bash
git status
```

If the manual run created or modified anything (e.g., `.patchfeld/` content) you do NOT want committed, the `.gitignore` already excludes `.patchfeld/`. Verify with `git status` that nothing under `.patchfeld/` appears.

```bash
git tag walking-skeleton-complete
```

---

## Self-review checklist (already run; for reviewer reference)

- [x] **Spec coverage:** every section of `2026-05-06-patchfeld-design.md` that's in scope for v1 is either implemented in this plan or explicitly deferred at the top with a pointer to the future plan.
- [x] **Placeholder scan:** no "TODO" / "TBD" / "implement later" in tasks; every code-touching step has the actual code.
- [x] **Type consistency:** `LayoutSpec`, `Panel`, `Container`, `MountPanel`, `UnmountPanel`, `UpdateProps`, `EventBus`, `UserMessageToOrchestrator`, `OrchestratorReply`, `TranscriptEntry`, `OrchestratorTranscript`, `FakeOrchestratorSession`, `WidgetRegistry`, `PatchfeldApp`, `CommandBar`, `StatusBar`, `OrchestratorChat`, `AgentTable`, `ActivityFeed` — names used identically across all tasks.
- [x] **Scope:** plan ends with a working app validated end-to-end, but does NOT include any feature deferred to plans 2–6 (real SDK, AgentManager, set_layout tools, config tools, custom widgets, Terminal widget, save/load named layouts, History view).
