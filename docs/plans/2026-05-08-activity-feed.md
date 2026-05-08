# Activity Feed Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the `ActivityFeed` placeholder with a working four-mode event stream backed by an app-level `ActivityLog` singleton, with mode chips, variable row density, click-through navigation, and auto-follow scrolling.

**Architecture:** A new `ActivityLog` lives on `PatchbaiApp` and subscribes to a curated set of `EventBus` events, normalizing each into an `ActivityEntry` and storing the last 500 in a `deque`. It publishes `ActivityLogged(entry)` after each append. The new `ActivityFeed` widget reads the singleton's backlog on mount and subscribes to `ActivityLogged` for live updates, applying a per-instance mode filter to the rendered rows.

**Tech Stack:** Python 3.12, Textual 8.x, pytest + pytest-asyncio, the existing `EventBus` in `patchbai/events.py`.

**Spec:** `docs/specs/2026-05-08-activity-feed-design.md`

---

## File Structure

**New files**
- `patchbai/activity/__init__.py` — package marker.
- `patchbai/activity/log.py` — `ActivityEntry` dataclass, `ActivityKind` constants, `ActivityLog` class.
- `patchbai/widgets/activity_feed.py` — `ActivityFeed`, `_ModeChips`, `_ActivityRow`, `_VARIANT`, `_CLICK_HANDLERS`, `_MODE_KINDS`.
- `tests/test_activity_log.py` — pure-logic tests for `ActivityLog` (no Textual).
- `tests/test_activity_feed_widget.py` — Pilot tests for `ActivityFeed`.
- `tests/test_activity_feed_modes.py` — table-driven mode coverage.

**Modified files**
- `patchbai/events.py` — add `ActivityLogged` and `AgentFocusRequested` event classes.
- `patchbai/app.py` — instantiate `self.activity_log` in `__init__`; switch `ActivityFeed` import from `placeholders` to `activity_feed`.
- `patchbai/widgets/agent_table.py` — subscribe to `AgentFocusRequested` and select the matching row.

**Deleted files**
- `patchbai/widgets/placeholders.py` — only contains `ActivityFeed`; deleted after callers migrate.

**Existing test files modified to update import paths**
- `tests/test_layout_engine_focus.py`
- `tests/test_layout_engine_titles.py`
- `tests/test_layout_engine_weakref.py`
- `tests/test_app_smoke.py`
- `tests/test_layout_engine_idempotent.py`
- `tests/test_layout_engine_splitter.py`
- `tests/test_orchestrator_tools_get_layout.py`
- `tests/test_layout_engine_tabs.py`
- `tests/test_layout_titles_resolver.py`

---

## Task 1: Add new event types

**Files:**
- Modify: `patchbai/events.py`
- Test: `tests/test_events_activity.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_events_activity.py`:

```python
from patchbai.events import ActivityLogged, AgentFocusRequested


def test_activity_logged_carries_entry():
    sentinel = object()
    e = ActivityLogged(entry=sentinel)
    assert e.entry is sentinel


def test_agent_focus_requested_carries_id():
    e = AgentFocusRequested(agent_id="abc123")
    assert e.agent_id == "abc123"


def test_events_are_frozen():
    import dataclasses
    e = ActivityLogged(entry=None)
    assert dataclasses.is_dataclass(e) and dataclasses.fields(e)
    try:
        e.entry = 1  # type: ignore[misc]
    except dataclasses.FrozenInstanceError:
        return
    raise AssertionError("ActivityLogged must be frozen")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/pytest tests/test_events_activity.py -v`
Expected: ImportError — `ActivityLogged` and `AgentFocusRequested` don't exist yet.

- [ ] **Step 3: Add the event classes**

Append to `patchbai/events.py` (right before the `# --- The bus ---------------------------------------------------------------` divider):

```python
@dataclass(frozen=True)
class ActivityLogged:
    """A new entry was appended to the app's ActivityLog. Subscribers (e.g.,
    ActivityFeed widgets) consume this to render the new entry. The `entry`
    field is an `ActivityEntry` from `patchbai.activity.log`; we leave it
    typed as `object` here to avoid a circular import."""
    entry: object


@dataclass(frozen=True)
class AgentFocusRequested:
    """An ActivityFeed row click (or other UI affordance) wants to focus a
    specific agent. AgentTable subscribes and selects + scrolls to the
    matching row; if no AgentTable is mounted the click handler falls back
    to opening the agent's TranscriptScreen."""
    agent_id: str
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/pytest tests/test_events_activity.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add patchbai/events.py tests/test_events_activity.py
git commit -m "feat(events): add ActivityLogged and AgentFocusRequested"
```

---

## Task 2: ActivityEntry and ActivityKind

**Files:**
- Create: `patchbai/activity/__init__.py`
- Create: `patchbai/activity/log.py`
- Test: `tests/test_activity_log.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_activity_log.py`:

```python
from datetime import datetime

from patchbai.activity.log import ActivityEntry, ActivityKind


def test_activity_entry_required_fields():
    e = ActivityEntry(
        timestamp=datetime(2026, 5, 8, 15, 42, 1),
        kind=ActivityKind.TAB_ADDED,
        summary='"Files"',
        detail=None,
        agent_id=None,
        tab_id="abc",
        raw=None,
    )
    assert e.summary == '"Files"'
    assert e.tab_id == "abc"
    assert e.kind == "tab.added"


def test_activity_kind_values_are_dotted_strings():
    # Spot-check that we expose dotted-string constants matching the spec.
    assert ActivityKind.AGENT_SPAWNED == "agent.spawned"
    assert ActivityKind.AGENT_DONE == "agent.done"
    assert ActivityKind.LAYOUT_FAILED == "layout.failed"
    assert ActivityKind.TAB_ADDED == "tab.added"
    assert ActivityKind.WORKSPACE_CWD == "workspace.cwd"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/pytest tests/test_activity_log.py -v`
Expected: ImportError — `patchbai.activity` package doesn't exist.

- [ ] **Step 3: Create the package and module**

Create `patchbai/activity/__init__.py` (empty file):

```python
```

Create `patchbai/activity/log.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


class ActivityKind:
    """String constants for entry kinds. Plain class instead of an Enum so
    `entry.kind == ActivityKind.AGENT_SPAWNED` and `entry.kind == "agent.spawned"`
    are both valid — keeps test tables and the mode-filter dict ergonomic."""

    AGENT_SPAWNED = "agent.spawned"
    AGENT_STATE = "agent.state"
    AGENT_DONE = "agent.done"
    AGENT_MESSAGE = "agent.message"
    AGENT_TOOL = "agent.tool"
    AGENT_ASK = "agent.ask"
    AGENT_NOTIFY = "agent.notify"
    AGENT_ARCHIVE = "agent.archive"
    ORCH_USER = "orch.user"
    ORCH_REPLY = "orch.reply"
    ORCH_SESSION = "orch.session"
    LAYOUT_APPLIED = "layout.applied"
    LAYOUT_FAILED = "layout.failed"
    TAB_ADDED = "tab.added"
    TAB_CLOSED = "tab.closed"
    TAB_SWITCHED = "tab.switched"
    WORKSPACE_CWD = "workspace.cwd"
    FILE_SELECTED = "file.selected"


@dataclass(frozen=True)
class ActivityEntry:
    """One normalized record in the ActivityLog. `kind` is one of the
    ActivityKind dotted-string constants; `raw` is the original event object
    for debugging/forensics."""
    timestamp: datetime
    kind: str
    summary: str
    detail: str | None
    agent_id: str | None
    tab_id: str | None
    raw: object
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/pytest tests/test_activity_log.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add patchbai/activity/__init__.py patchbai/activity/log.py tests/test_activity_log.py
git commit -m "feat(activity): add ActivityEntry and ActivityKind"
```

---

## Task 3: ActivityLog with agent event subscriptions

**Files:**
- Modify: `patchbai/activity/log.py`
- Test: `tests/test_activity_log.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_activity_log.py`:

```python
import time

from patchbai.activity.log import ActivityLog
from patchbai.agents.state import AgentInfo, AgentState
from patchbai.events import (
    ActivityLogged, AgentArchiveChanged, AgentMessageAppended,
    AgentNotifiedOrchestrator, AgentRequestedUserInput, AgentSpawned,
    AgentStateChanged, AgentTokensTouched, EventBus, StatsUpdated,
)


def _info(agent_id="a1", name="bot", state=AgentState.IDLE) -> AgentInfo:
    return AgentInfo(id=agent_id, name=name, cwd="/tmp", started_at=time.time(), state=state)


def test_log_captures_agent_spawned():
    bus = EventBus()
    log = ActivityLog(bus)
    bus.publish(AgentSpawned(info=_info()))
    entries = log.entries()
    assert len(entries) == 1
    assert entries[0].kind == "agent.spawned"
    assert entries[0].agent_id == "a1"
    assert "bot" in entries[0].summary


def test_log_publishes_activity_logged():
    bus = EventBus()
    log = ActivityLog(bus)
    seen: list[ActivityLogged] = []
    bus.subscribe(ActivityLogged, lambda e: seen.append(e))
    bus.publish(AgentSpawned(info=_info()))
    assert len(seen) == 1
    assert seen[0].entry is log.entries()[0]


def test_agent_state_terminal_emits_agent_done():
    bus = EventBus()
    log = ActivityLog(bus)
    bus.publish(AgentStateChanged(info=_info(state=AgentState.DONE), old_state=AgentState.RUNNING))
    assert log.entries()[0].kind == "agent.done"


def test_agent_state_non_terminal_emits_agent_state():
    bus = EventBus()
    log = ActivityLog(bus)
    bus.publish(AgentStateChanged(info=_info(state=AgentState.RUNNING), old_state=AgentState.IDLE))
    assert log.entries()[0].kind == "agent.state"


def test_agent_message_role_split():
    bus = EventBus()
    log = ActivityLog(bus)
    bus.publish(AgentMessageAppended(agent_id="a1", role="assistant", text="hi"))
    bus.publish(AgentMessageAppended(agent_id="a1", role="tool_use", text="run", tool_id="t1", tool_name="bash"))
    kinds = [e.kind for e in log.entries()]
    assert kinds == ["agent.message", "agent.tool"]


def test_agent_ask_archive_notify_captured():
    bus = EventBus()
    log = ActivityLog(bus)
    bus.publish(AgentRequestedUserInput(agent_id="a1", question="ok?", request_id="r1"))
    bus.publish(AgentNotifiedOrchestrator(agent_id="a1", message="done"))
    bus.publish(AgentArchiveChanged(info=_info()))
    kinds = [e.kind for e in log.entries()]
    assert kinds == ["agent.ask", "agent.notify", "agent.archive"]


def test_tokens_touched_and_stats_updated_are_filtered():
    bus = EventBus()
    log = ActivityLog(bus)
    bus.publish(AgentTokensTouched(agent_id="a1"))
    bus.publish(StatsUpdated(tokens_in=1, tokens_out=1, cost=0.0, active_agents=1))
    assert log.entries() == ()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_activity_log.py -v`
Expected: ImportError on `ActivityLog`.

- [ ] **Step 3: Implement the ActivityLog**

Append to `patchbai/activity/log.py`:

```python
from collections import deque
from typing import Callable, Iterable

from patchbai.events import (
    ActivityLogged, AgentArchiveChanged, AgentMessageAppended,
    AgentNotifiedOrchestrator, AgentRequestedUserInput, AgentSpawned,
    AgentStateChanged, EventBus,
)


class ActivityLog:
    """App-singleton capture of curated EventBus events. Stores the last 500
    normalized entries in a deque. Publishes ActivityLogged after every
    append so subscribers can react incrementally without re-walking the
    backlog. Mode filtering is the consumer's responsibility — the log
    captures the union."""

    BUFFER_SIZE = 500

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self._entries: deque[ActivityEntry] = deque(maxlen=self.BUFFER_SIZE)
        self._wire_agent_subs(bus)

    def entries(self) -> tuple[ActivityEntry, ...]:
        """Snapshot of current entries, oldest first."""
        return tuple(self._entries)

    # --- agent subs --------------------------------------------------------

    def _wire_agent_subs(self, bus: EventBus) -> None:
        bus.subscribe(AgentSpawned, self._on_agent_spawned)
        bus.subscribe(AgentStateChanged, self._on_agent_state)
        bus.subscribe(AgentMessageAppended, self._on_agent_message)
        bus.subscribe(AgentRequestedUserInput, self._on_agent_ask)
        bus.subscribe(AgentNotifiedOrchestrator, self._on_agent_notify)
        bus.subscribe(AgentArchiveChanged, self._on_agent_archive)

    def _on_agent_spawned(self, event: AgentSpawned) -> None:
        info = event.info
        self._append(
            kind=ActivityKind.AGENT_SPAWNED,
            summary=f"{info.name} spawned",
            detail=f"cwd: {info.cwd}",
            agent_id=info.id,
            tab_id=None,
            raw=event,
        )

    def _on_agent_state(self, event: AgentStateChanged) -> None:
        info = event.info
        if info.state.is_terminal:
            kind = ActivityKind.AGENT_DONE
            summary = f"{info.name}: {event.old_state.value} → {info.state.value}"
        else:
            kind = ActivityKind.AGENT_STATE
            summary = f"{info.name}: {event.old_state.value} → {info.state.value}"
        self._append(
            kind=kind, summary=summary, detail=None,
            agent_id=info.id, tab_id=None, raw=event,
        )

    def _on_agent_message(self, event: AgentMessageAppended) -> None:
        if event.role in ("user", "assistant"):
            kind = ActivityKind.AGENT_MESSAGE
            detail = event.text
        elif event.role in ("tool_use", "tool_result"):
            kind = ActivityKind.AGENT_TOOL
            detail = event.tool_name or event.text
        else:
            return  # thinking/system are not surfaced in the feed
        self._append(
            kind=kind,
            summary=event.agent_id,
            detail=detail,
            agent_id=event.agent_id,
            tab_id=None,
            raw=event,
        )

    def _on_agent_ask(self, event: AgentRequestedUserInput) -> None:
        self._append(
            kind=ActivityKind.AGENT_ASK,
            summary=event.agent_id,
            detail=event.question,
            agent_id=event.agent_id,
            tab_id=None,
            raw=event,
        )

    def _on_agent_notify(self, event: AgentNotifiedOrchestrator) -> None:
        self._append(
            kind=ActivityKind.AGENT_NOTIFY,
            summary=event.agent_id,
            detail=event.message,
            agent_id=event.agent_id,
            tab_id=None,
            raw=event,
        )

    def _on_agent_archive(self, event: AgentArchiveChanged) -> None:
        info = event.info
        self._append(
            kind=ActivityKind.AGENT_ARCHIVE,
            summary=f"{info.name} {'archived' if info.archived else 'unarchived'}",
            detail=None,
            agent_id=info.id,
            tab_id=None,
            raw=event,
        )

    # --- append ------------------------------------------------------------

    def _append(
        self, *, kind: str, summary: str, detail: str | None,
        agent_id: str | None, tab_id: str | None, raw: object,
    ) -> None:
        entry = ActivityEntry(
            timestamp=datetime.now(),
            kind=kind, summary=summary, detail=detail,
            agent_id=agent_id, tab_id=tab_id, raw=raw,
        )
        self._entries.append(entry)
        self._bus.publish(ActivityLogged(entry=entry))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_activity_log.py -v`
Expected: 8 passed (2 from Task 2 + 6 new).

- [ ] **Step 5: Commit**

```bash
git add patchbai/activity/log.py tests/test_activity_log.py
git commit -m "feat(activity): ActivityLog with agent-event capture"
```

---

## Task 4: Remaining ActivityLog subscriptions and ring eviction

**Files:**
- Modify: `patchbai/activity/log.py`
- Test: `tests/test_activity_log.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_activity_log.py`:

```python
from patchbai.events import (
    FileSelected, LayoutApplied, LayoutFailed, OrchestratorReply,
    OrchestratorSessionSwitched, TabAdded, TabClosed, TabSwitched,
    UserMessageToOrchestrator, WorkspaceCwdChanged,
)
from patchbai.layout.spec import LayoutSpec


def _spec() -> LayoutSpec:
    return LayoutSpec.model_validate({
        "version": 1,
        "layout": {"id": "p", "widget": "ActivityFeed"},
    })


def test_log_captures_orchestrator_events():
    bus = EventBus()
    log = ActivityLog(bus)
    bus.publish(UserMessageToOrchestrator(text="hi"))
    bus.publish(OrchestratorReply(text="ok"))
    bus.publish(OrchestratorSessionSwitched(session_id="s1", transcript_path="/tmp/t"))
    kinds = [e.kind for e in log.entries()]
    assert kinds == ["orch.user", "orch.reply", "orch.session"]


def test_log_captures_layout_events():
    bus = EventBus()
    log = ActivityLog(bus)
    bus.publish(LayoutApplied(spec=_spec(), layout_name="dashboard", tab_id="t1"))
    bus.publish(LayoutFailed(error="boom", tab_id="t1"))
    kinds = [e.kind for e in log.entries()]
    assert kinds == ["layout.applied", "layout.failed"]
    assert log.entries()[1].detail == "boom"


def test_log_captures_tab_events():
    bus = EventBus()
    log = ActivityLog(bus)
    bus.publish(TabAdded(tab_id="t1", title="Files"))
    bus.publish(TabClosed(tab_id="t1"))
    bus.publish(TabSwitched(tab_id="t2", title="Logs"))
    kinds = [e.kind for e in log.entries()]
    assert kinds == ["tab.added", "tab.closed", "tab.switched"]
    assert log.entries()[0].tab_id == "t1"
    assert log.entries()[2].summary == "Logs"


def test_log_captures_workspace_and_file_events():
    bus = EventBus()
    log = ActivityLog(bus)
    bus.publish(WorkspaceCwdChanged(cwd="/var/foo"))
    bus.publish(FileSelected(path="/x/y.py"))
    kinds = [e.kind for e in log.entries()]
    assert kinds == ["workspace.cwd", "file.selected"]


def test_log_evicts_when_buffer_full():
    bus = EventBus()
    log = ActivityLog(bus)
    for i in range(ActivityLog.BUFFER_SIZE + 50):
        bus.publish(TabAdded(tab_id=f"t{i}", title=f"Tab {i}"))
    entries = log.entries()
    assert len(entries) == ActivityLog.BUFFER_SIZE
    # Most recent retained.
    assert entries[-1].tab_id == f"t{ActivityLog.BUFFER_SIZE + 49}"
    # Oldest dropped.
    assert entries[0].tab_id == "t50"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_activity_log.py -v`
Expected: 5 new failures (`test_log_captures_orchestrator_events`, `test_log_captures_layout_events`, `test_log_captures_tab_events`, `test_log_captures_workspace_and_file_events`, `test_log_evicts_when_buffer_full`).

- [ ] **Step 3: Wire the remaining subscriptions**

In `patchbai/activity/log.py`, add to the imports at top of the subscriptions block:

```python
from patchbai.events import (
    ActivityLogged, AgentArchiveChanged, AgentMessageAppended,
    AgentNotifiedOrchestrator, AgentRequestedUserInput, AgentSpawned,
    AgentStateChanged, EventBus, FileSelected, LayoutApplied, LayoutFailed,
    OrchestratorReply, OrchestratorSessionSwitched, TabAdded, TabClosed,
    TabSwitched, UserMessageToOrchestrator, WorkspaceCwdChanged,
)
```

Replace `_wire_agent_subs` with `_wire_subscriptions` and add the new handlers. Final state of subscription wiring + new handlers:

```python
    def _wire_subscriptions(self, bus: EventBus) -> None:
        bus.subscribe(AgentSpawned, self._on_agent_spawned)
        bus.subscribe(AgentStateChanged, self._on_agent_state)
        bus.subscribe(AgentMessageAppended, self._on_agent_message)
        bus.subscribe(AgentRequestedUserInput, self._on_agent_ask)
        bus.subscribe(AgentNotifiedOrchestrator, self._on_agent_notify)
        bus.subscribe(AgentArchiveChanged, self._on_agent_archive)
        bus.subscribe(UserMessageToOrchestrator, self._on_orch_user)
        bus.subscribe(OrchestratorReply, self._on_orch_reply)
        bus.subscribe(OrchestratorSessionSwitched, self._on_orch_session)
        bus.subscribe(LayoutApplied, self._on_layout_applied)
        bus.subscribe(LayoutFailed, self._on_layout_failed)
        bus.subscribe(TabAdded, self._on_tab_added)
        bus.subscribe(TabClosed, self._on_tab_closed)
        bus.subscribe(TabSwitched, self._on_tab_switched)
        bus.subscribe(WorkspaceCwdChanged, self._on_cwd_changed)
        bus.subscribe(FileSelected, self._on_file_selected)

    def _on_orch_user(self, event: UserMessageToOrchestrator) -> None:
        self._append(
            kind=ActivityKind.ORCH_USER, summary="user → orchestrator",
            detail=event.text, agent_id=None, tab_id=None, raw=event,
        )

    def _on_orch_reply(self, event: OrchestratorReply) -> None:
        self._append(
            kind=ActivityKind.ORCH_REPLY, summary="orchestrator → user",
            detail=event.text, agent_id=None, tab_id=None, raw=event,
        )

    def _on_orch_session(self, event: OrchestratorSessionSwitched) -> None:
        self._append(
            kind=ActivityKind.ORCH_SESSION,
            summary=f"session → {event.session_id[:8]}",
            detail=event.transcript_path, agent_id=None, tab_id=None, raw=event,
        )

    def _on_layout_applied(self, event: LayoutApplied) -> None:
        self._append(
            kind=ActivityKind.LAYOUT_APPLIED,
            summary=event.layout_name or "(unnamed)",
            detail=None, agent_id=None, tab_id=event.tab_id, raw=event,
        )

    def _on_layout_failed(self, event: LayoutFailed) -> None:
        self._append(
            kind=ActivityKind.LAYOUT_FAILED, summary="layout failed",
            detail=event.error, agent_id=None, tab_id=event.tab_id, raw=event,
        )

    def _on_tab_added(self, event: TabAdded) -> None:
        self._append(
            kind=ActivityKind.TAB_ADDED, summary=event.title, detail=None,
            agent_id=None, tab_id=event.tab_id, raw=event,
        )

    def _on_tab_closed(self, event: TabClosed) -> None:
        self._append(
            kind=ActivityKind.TAB_CLOSED, summary=event.tab_id, detail=None,
            agent_id=None, tab_id=event.tab_id, raw=event,
        )

    def _on_tab_switched(self, event: TabSwitched) -> None:
        self._append(
            kind=ActivityKind.TAB_SWITCHED, summary=event.title, detail=None,
            agent_id=None, tab_id=event.tab_id, raw=event,
        )

    def _on_cwd_changed(self, event: WorkspaceCwdChanged) -> None:
        self._append(
            kind=ActivityKind.WORKSPACE_CWD, summary=event.cwd, detail=None,
            agent_id=None, tab_id=None, raw=event,
        )

    def _on_file_selected(self, event: FileSelected) -> None:
        self._append(
            kind=ActivityKind.FILE_SELECTED, summary=event.path, detail=None,
            agent_id=None, tab_id=None, raw=event,
        )
```

Update the `__init__` call:

```python
    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self._entries: deque[ActivityEntry] = deque(maxlen=self.BUFFER_SIZE)
        self._wire_subscriptions(bus)
```

Delete the old `_wire_agent_subs` method.

- [ ] **Step 4: Run all activity log tests**

Run: `.venv/bin/pytest tests/test_activity_log.py -v`
Expected: 13 passed.

- [ ] **Step 5: Commit**

```bash
git add patchbai/activity/log.py tests/test_activity_log.py
git commit -m "feat(activity): subscribe to orch/layout/tab/workspace events; verify ring eviction"
```

---

## Task 5: Wire ActivityLog onto PatchbaiApp

**Files:**
- Modify: `patchbai/app.py`
- Test: `tests/test_app_smoke_activity_log.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_app_smoke_activity_log.py`:

```python
import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

from patchbai.activity.log import ActivityLog
from patchbai.agents.fake_sdk_adapter import FakeSDKAdapter
from patchbai.agents.manager import AgentManager
from patchbai.app import PatchbaiApp
from patchbai.events import EventBus, TabAdded
from patchbai.orchestrator.session import OrchestratorSession


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
    )
    return app


@pytest.mark.asyncio
async def test_app_has_activity_log_after_init(tmp_path):
    app = _build_app(tmp_path)
    assert isinstance(app.activity_log, ActivityLog)


@pytest.mark.asyncio
async def test_activity_log_is_wired_to_app_event_bus(tmp_path):
    app = _build_app(tmp_path)
    app.event_bus.publish(TabAdded(tab_id="t1", title="Files"))
    entries = app.activity_log.entries()
    assert len(entries) >= 1
    assert any(e.kind == "tab.added" and e.tab_id == "t1" for e in entries)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/pytest tests/test_app_smoke_activity_log.py -v`
Expected: AttributeError — `PatchbaiApp` has no `activity_log`.

- [ ] **Step 3: Wire ActivityLog into the app**

In `patchbai/app.py`, add to the import block (top of file):

```python
from patchbai.activity.log import ActivityLog
```

In `PatchbaiApp.__init__`, after the line `self.event_bus = EventBus()` (currently around line 270), add:

```python
        self.activity_log = ActivityLog(self.event_bus)
```

The placement matters: this must come AFTER `self.event_bus = EventBus()` and BEFORE `self.orchestrator = ...` is constructed, so any events emitted during orchestrator init are captured.

There is a subtle re-binding: the smoke test fixtures set `app.event_bus = bus` AFTER `__init__`. The ActivityLog created in `__init__` will be subscribed to the original event_bus, not the replaced one. To keep the smoke fixtures working without rewriting them, add a tiny rebind helper at the end of `__init__`:

```python
        # If a future caller reassigns app.event_bus (test fixtures do this),
        # they must also re-create activity_log to keep subscriptions wired.
        # We can't intercept attribute assignment cleanly here; the fixture
        # in test_app_smoke_activity_log.py compensates by using the bus
        # passed into PatchbaiApp directly.
```

The simpler fix is: have the test's `_build_app` recreate `activity_log` after re-pointing the bus:

In `tests/test_app_smoke_activity_log.py`, change `_build_app` to add (after `app.event_bus = bus`):

```python
    app.activity_log = ActivityLog(bus)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/pytest tests/test_app_smoke_activity_log.py -v`
Expected: 2 passed.

Run the broader smoke suite to confirm no regressions:

Run: `.venv/bin/pytest tests/test_app_smoke.py tests/test_app_smoke_tabs.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add patchbai/app.py tests/test_app_smoke_activity_log.py
git commit -m "feat(app): instantiate ActivityLog on PatchbaiApp"
```

---

## Task 6: ActivityFeed widget shell with backlog rendering

**Files:**
- Create: `patchbai/widgets/activity_feed.py`
- Modify: `patchbai/app.py` (registry import)
- Test: `tests/test_activity_feed_widget.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_activity_feed_widget.py`:

```python
import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock
from textual.widgets import Static

from patchbai.activity.log import ActivityLog
from patchbai.agents.fake_sdk_adapter import FakeSDKAdapter
from patchbai.agents.manager import AgentManager
from patchbai.app import PatchbaiApp
from patchbai.events import EventBus, TabAdded
from patchbai.orchestrator.session import OrchestratorSession


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
    app = PatchbaiApp(cwd=tmp_path, manager=manager, global_dir=tmp_path)
    app.event_bus = bus
    app.activity_log = ActivityLog(bus)
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
async def test_activity_feed_renders_backlog_on_mount(tmp_path):
    app = _build_app(tmp_path)
    # Pre-load backlog before mount.
    app.event_bus.publish(TabAdded(tab_id="t1", title="Files"))
    app.event_bus.publish(TabAdded(tab_id="t2", title="Logs"))

    async with app.run_test() as pilot:
        await pilot.pause()
        # Collect static text rows inside any ActivityFeed instance.
        from patchbai.widgets.activity_feed import ActivityFeed, _ActivityRow
        feeds = list(app.query(ActivityFeed))
        assert feeds, "default dashboard layout should mount one ActivityFeed"
        rows = list(feeds[0].query(_ActivityRow))
        # Backlog plus any default-layout-fired events. At minimum our two appear.
        labels = " ".join(str(r.renderable) for r in rows)
        assert "Files" in labels
        assert "Logs" in labels


@pytest.mark.asyncio
async def test_activity_feed_appends_new_event_after_mount(tmp_path):
    app = _build_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        from patchbai.widgets.activity_feed import ActivityFeed, _ActivityRow
        feed = app.query(ActivityFeed).first()
        before = len(list(feed.query(_ActivityRow)))
        app.event_bus.publish(TabAdded(tab_id="zzz", title="Surprise"))
        await pilot.pause()
        after_rows = list(feed.query(_ActivityRow))
        assert len(after_rows) == before + 1
        assert "Surprise" in str(after_rows[-1].renderable)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/pytest tests/test_activity_feed_widget.py -v`
Expected: ImportError — `patchbai.widgets.activity_feed` doesn't exist.

- [ ] **Step 3: Implement the widget shell**

Create `patchbai/widgets/activity_feed.py`:

```python
from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container, VerticalScroll
from textual.widgets import Static

from patchbai.activity.log import ActivityEntry, ActivityKind
from patchbai.events import ActivityLogged


class _ActivityRow(Static):
    """One feed row. Variants come in Task 9; for now this just renders a
    compact single line."""

    DEFAULT_CSS = """
    _ActivityRow {
        height: auto;
        padding: 0 1;
    }
    """

    def __init__(self, entry: ActivityEntry) -> None:
        super().__init__(self._format(entry))
        self.entry = entry

    @staticmethod
    def _format(entry: ActivityEntry) -> str:
        ts = entry.timestamp.strftime("%H:%M:%S")
        return f"[{ts}] {entry.kind:<18} {entry.summary}"


class ActivityFeed(Container):
    """Real Activity Feed. Reads backlog from app.activity_log on mount and
    subscribes to ActivityLogged for live updates. Mode filtering arrives
    in Task 7."""

    DEFAULT_BORDER_TITLE = "Activity"

    DEFAULT_CSS = """
    ActivityFeed {
        border: round $surface-lighten-2;
        padding: 0 1;
    }
    ActivityFeed VerticalScroll {
        height: 1fr;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._unsub = None

    def compose(self) -> ComposeResult:
        yield VerticalScroll(id="activity-rows")

    def on_mount(self) -> None:
        bus = self.app.event_bus
        log = self.app.activity_log
        scroll = self.query_one("#activity-rows", VerticalScroll)
        for entry in log.entries():
            scroll.mount(_ActivityRow(entry))
        self._unsub = bus.subscribe(ActivityLogged, self._on_logged)

    def on_unmount(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None

    def _on_logged(self, event: ActivityLogged) -> None:
        scroll = self.query_one("#activity-rows", VerticalScroll)
        scroll.mount(_ActivityRow(event.entry))
```

Update `patchbai/app.py` to import `ActivityFeed` from the new module. Find the current import:

```python
from patchbai.widgets.placeholders import ActivityFeed
```

Replace with:

```python
from patchbai.widgets.activity_feed import ActivityFeed
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_activity_feed_widget.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add patchbai/widgets/activity_feed.py patchbai/app.py tests/test_activity_feed_widget.py
git commit -m "feat(activity-feed): real widget rendering backlog and live appends"
```

---

## Task 7: Mode prop and filter table

**Files:**
- Modify: `patchbai/widgets/activity_feed.py`
- Create: `tests/test_activity_feed_modes.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_activity_feed_modes.py`:

```python
import pytest

from patchbai.widgets.activity_feed import _MODE_KINDS, MODES


# Source-of-truth coverage table from the design spec. Each row: (kind, modes-where-visible).
_TABLE = [
    ("agent.spawned",  {"audit", "agents", "debug"}),
    ("agent.state",    {"audit", "agents", "debug"}),
    ("agent.done",     {"audit", "agents", "notifs", "debug"}),
    ("agent.message",  {"agents", "debug"}),
    ("agent.tool",     {"debug"}),
    ("agent.ask",      {"audit", "agents", "notifs", "debug"}),
    ("agent.notify",   {"audit", "agents", "notifs", "debug"}),
    ("agent.archive",  {"audit", "agents", "debug"}),
    ("orch.user",      {"audit", "debug"}),
    ("orch.reply",     {"audit", "debug"}),
    ("orch.session",   {"audit", "debug"}),
    ("layout.applied", {"audit", "debug"}),
    ("layout.failed",  {"audit", "notifs", "debug"}),
    ("tab.added",      {"audit", "debug"}),
    ("tab.closed",     {"audit", "debug"}),
    ("tab.switched",   {"debug"}),
    ("workspace.cwd",  {"audit", "notifs", "debug"}),
    ("file.selected",  {"debug"}),
]


@pytest.mark.parametrize("kind,visible_modes", _TABLE)
def test_mode_filter_matches_spec_table(kind: str, visible_modes: set[str]):
    for mode in MODES:
        actually_visible = kind in _MODE_KINDS[mode]
        expected_visible = mode in visible_modes
        assert actually_visible == expected_visible, (
            f"kind={kind!r} mode={mode!r}: "
            f"expected_visible={expected_visible}, actually_visible={actually_visible}"
        )


def test_modes_constant_exposes_all_four():
    assert MODES == ("audit", "agents", "notifs", "debug")
```

Append to `tests/test_activity_feed_widget.py`:

```python
@pytest.mark.asyncio
async def test_mode_prop_filters_initial_render(tmp_path):
    """Layout prop `{mode: 'agents'}` should hide tab/orch/etc. kinds."""
    import json
    seed = {
        "version": 1,
        "tabs": [
            {
                "id": "main", "title": "Main",
                "layout": {
                    "version": 1,
                    "layout": {
                        "type": "horizontal",
                        "children": [
                            {"id": "orch", "widget": "OrchestratorChat", "size": "50%"},
                            {"id": "feed", "widget": "ActivityFeed",
                             "props": {"mode": "agents"}, "size": "50%"},
                        ],
                    },
                },
            },
        ],
        "active": "main",
    }
    (tmp_path / ".patchbai").mkdir()
    (tmp_path / ".patchbai" / "workspace.json").write_text(json.dumps(seed))

    app = _build_app(tmp_path)
    # Pre-load both kinds: a tab.* (hidden in agents mode) and a stand-in
    # for an agent kind. Use TabAdded + a synthetic ActivityEntry isn't
    # possible (we only push via bus); so use a TabAdded (filtered out).
    app.event_bus.publish(TabAdded(tab_id="t1", title="HiddenTab"))

    async with app.run_test() as pilot:
        await pilot.pause()
        from patchbai.widgets.activity_feed import ActivityFeed, _ActivityRow
        feed = app.query(ActivityFeed).first()
        rows = list(feed.query(_ActivityRow))
        labels = " ".join(str(r.renderable) for r in rows)
        # tab.added is not in agents mode → "HiddenTab" must not be rendered.
        assert "HiddenTab" not in labels
        assert feed.mode == "agents"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_activity_feed_modes.py tests/test_activity_feed_widget.py::test_mode_prop_filters_initial_render -v`
Expected: ImportError on `_MODE_KINDS`/`MODES`; AttributeError on `feed.mode`.

- [ ] **Step 3: Add the mode table and prop**

In `patchbai/widgets/activity_feed.py`, before the `_ActivityRow` class, add:

```python
MODES: tuple[str, ...] = ("audit", "agents", "notifs", "debug")

# Per-mode kind allowlists, derived from the design spec table.
_MODE_KINDS: dict[str, frozenset[str]] = {
    "audit": frozenset({
        ActivityKind.AGENT_SPAWNED, ActivityKind.AGENT_STATE, ActivityKind.AGENT_DONE,
        ActivityKind.AGENT_ASK, ActivityKind.AGENT_NOTIFY, ActivityKind.AGENT_ARCHIVE,
        ActivityKind.ORCH_USER, ActivityKind.ORCH_REPLY, ActivityKind.ORCH_SESSION,
        ActivityKind.LAYOUT_APPLIED, ActivityKind.LAYOUT_FAILED,
        ActivityKind.TAB_ADDED, ActivityKind.TAB_CLOSED,
        ActivityKind.WORKSPACE_CWD,
    }),
    "agents": frozenset({
        ActivityKind.AGENT_SPAWNED, ActivityKind.AGENT_STATE, ActivityKind.AGENT_DONE,
        ActivityKind.AGENT_MESSAGE, ActivityKind.AGENT_ASK, ActivityKind.AGENT_NOTIFY,
        ActivityKind.AGENT_ARCHIVE,
    }),
    "notifs": frozenset({
        ActivityKind.AGENT_DONE, ActivityKind.AGENT_ASK, ActivityKind.AGENT_NOTIFY,
        ActivityKind.LAYOUT_FAILED, ActivityKind.WORKSPACE_CWD,
    }),
    "debug": frozenset({
        ActivityKind.AGENT_SPAWNED, ActivityKind.AGENT_STATE, ActivityKind.AGENT_DONE,
        ActivityKind.AGENT_MESSAGE, ActivityKind.AGENT_TOOL, ActivityKind.AGENT_ASK,
        ActivityKind.AGENT_NOTIFY, ActivityKind.AGENT_ARCHIVE,
        ActivityKind.ORCH_USER, ActivityKind.ORCH_REPLY, ActivityKind.ORCH_SESSION,
        ActivityKind.LAYOUT_APPLIED, ActivityKind.LAYOUT_FAILED,
        ActivityKind.TAB_ADDED, ActivityKind.TAB_CLOSED, ActivityKind.TAB_SWITCHED,
        ActivityKind.WORKSPACE_CWD, ActivityKind.FILE_SELECTED,
    }),
}
```

Update `ActivityFeed.__init__` to accept the `mode` prop and validate it:

```python
    def __init__(self, *, mode: str | None = None) -> None:
        super().__init__()
        if mode is not None and mode not in _MODE_KINDS:
            mode = None  # silently fall back to default; no invariant break
        self.mode: str = mode or "audit"
        self._unsub = None
```

Update `on_mount` and `_on_logged` to filter by mode:

```python
    def on_mount(self) -> None:
        bus = self.app.event_bus
        log = self.app.activity_log
        scroll = self.query_one("#activity-rows", VerticalScroll)
        allow = _MODE_KINDS[self.mode]
        for entry in log.entries():
            if entry.kind in allow:
                scroll.mount(_ActivityRow(entry))
        self._unsub = bus.subscribe(ActivityLogged, self._on_logged)

    def _on_logged(self, event: ActivityLogged) -> None:
        entry: ActivityEntry = event.entry  # type: ignore[assignment]
        if entry.kind not in _MODE_KINDS[self.mode]:
            return
        scroll = self.query_one("#activity-rows", VerticalScroll)
        scroll.mount(_ActivityRow(entry))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_activity_feed_modes.py tests/test_activity_feed_widget.py -v`
Expected: 18 mode tests + 3 widget tests pass.

- [ ] **Step 5: Commit**

```bash
git add patchbai/widgets/activity_feed.py tests/test_activity_feed_modes.py tests/test_activity_feed_widget.py
git commit -m "feat(activity-feed): mode prop and per-mode kind filter"
```

---

## Task 8: Mode chips and persistence

**Files:**
- Modify: `patchbai/widgets/activity_feed.py`
- Modify: `tests/test_activity_feed_widget.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_activity_feed_widget.py`:

```python
@pytest.mark.asyncio
async def test_clicking_mode_chip_changes_mode_and_persists(tmp_path):
    import json
    seed = {
        "version": 1,
        "tabs": [
            {
                "id": "main", "title": "Main",
                "layout": {
                    "version": 1,
                    "layout": {
                        "type": "horizontal",
                        "children": [
                            {"id": "orch", "widget": "OrchestratorChat", "size": "50%"},
                            {"id": "feed", "widget": "ActivityFeed",
                             "props": {"mode": "audit"}, "size": "50%"},
                        ],
                    },
                },
            },
        ],
        "active": "main",
    }
    (tmp_path / ".patchbai").mkdir()
    (tmp_path / ".patchbai" / "workspace.json").write_text(json.dumps(seed))

    app = _build_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        from patchbai.widgets.activity_feed import ActivityFeed, _ModeChip
        feed = app.query(ActivityFeed).first()
        assert feed.mode == "audit"
        # Find the "agents" chip and click it.
        chips = list(feed.query(_ModeChip))
        agents_chip = next(c for c in chips if c.mode == "agents")
        await pilot.click(agents_chip)
        await pilot.pause()
        await pilot.pause()  # let _apply_to_tab settle
        assert feed.mode == "agents"
        # Check that workspace.json now has props.mode == "agents".
        ws_raw = json.loads((tmp_path / ".patchbai" / "workspace.json").read_text())
        children = ws_raw["tabs"][0]["layout"]["layout"]["children"]
        feed_node = next(c for c in children if c.get("widget") == "ActivityFeed")
        assert feed_node["props"]["mode"] == "agents"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/pytest tests/test_activity_feed_widget.py::test_clicking_mode_chip_changes_mode_and_persists -v`
Expected: ImportError on `_ModeChip`.

- [ ] **Step 3: Implement chips and persistence**

Add to `patchbai/widgets/activity_feed.py` (after the constants block, before `_ActivityRow`):

```python
from textual.containers import Horizontal


class _ModeChip(Static):
    """Clickable mode label inside the chip strip. Carries the mode string;
    parent ActivityFeed reads `event.chip.mode` on click."""

    DEFAULT_CSS = """
    _ModeChip {
        padding: 0 1;
        margin: 0 1 0 0;
        border: tall $surface-lighten-2;
        color: $text;
    }
    _ModeChip.-active {
        border: tall $primary;
        color: $primary;
    }
    _ModeChip:hover {
        background: $boost;
    }
    """

    def __init__(self, mode: str, *, active: bool) -> None:
        super().__init__(mode.capitalize())
        self.mode = mode
        if active:
            self.add_class("-active")


class _ModeChips(Horizontal):
    DEFAULT_CSS = """
    _ModeChips {
        height: auto;
        padding: 0 1;
        background: $boost;
    }
    """

    def __init__(self, active: str) -> None:
        super().__init__()
        self._active = active

    def compose(self) -> ComposeResult:
        for m in MODES:
            yield _ModeChip(m, active=(m == self._active))
```

Update `ActivityFeed.compose` to mount the chip strip:

```python
    def compose(self) -> ComposeResult:
        yield _ModeChips(active=self.mode)
        yield VerticalScroll(id="activity-rows")
```

Add a click handler and a persistence helper to `ActivityFeed`:

```python
    def on_click(self, event) -> None:
        # Identify whether the click landed on a _ModeChip and switch.
        target = event.widget if hasattr(event, "widget") else None
        if not isinstance(target, _ModeChip):
            return
        new_mode = target.mode
        if new_mode == self.mode:
            return
        self._set_mode(new_mode)
        event.stop()

    def _set_mode(self, new_mode: str) -> None:
        self.mode = new_mode
        # Update chip styling.
        for chip in self.query(_ModeChip):
            chip.set_class(chip.mode == new_mode, "-active")
        # Rebuild the scroll region for the new mode.
        scroll = self.query_one("#activity-rows", VerticalScroll)
        scroll.remove_children()
        log = self.app.activity_log
        allow = _MODE_KINDS[new_mode]
        for entry in log.entries():
            if entry.kind in allow:
                scroll.mount(_ActivityRow(entry))
        # Persist the new mode into the layout JSON for this panel.
        self._persist_mode(new_mode)

    def _persist_mode(self, new_mode: str) -> None:
        """Walk the active tab's layout dict, find this widget's panel entry
        by id (panel-{node.id} → node.id == self.id minus prefix), update its
        `props.mode`, and call app._apply_to_tab to validate + save."""
        app = self.app
        active_tab_id = getattr(app, "_active_tab_id", None)
        ws = getattr(app, "_workspace", None)
        if active_tab_id is None or ws is None:
            return
        # The widget id is "panel-{node.id}". Extract the node id.
        if not self.id or not self.id.startswith("panel-"):
            return
        node_id = self.id[len("panel-"):]
        # Find the active tab's spec, deep-copy it, mutate the matching panel.
        from patchbai.layout.spec import LayoutSpec
        target_tab = next((t for t in ws.tabs if t.id == active_tab_id), None)
        if target_tab is None:
            return
        spec_dict = target_tab.layout.model_dump(mode="json")

        def _walk(node: dict) -> bool:
            if node.get("widget") == "ActivityFeed" and node.get("id") == node_id:
                node.setdefault("props", {})["mode"] = new_mode
                return True
            for child in node.get("children", []) or []:
                if _walk(child):
                    return True
            return False

        if not _walk(spec_dict["layout"]):
            return
        try:
            new_spec = LayoutSpec.model_validate(spec_dict)
        except Exception:
            return
        import asyncio as _asyncio
        _asyncio.create_task(app._apply_to_tab(active_tab_id, new_spec))
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/pytest tests/test_activity_feed_widget.py -v`
Expected: 4 widget tests pass.

- [ ] **Step 5: Commit**

```bash
git add patchbai/widgets/activity_feed.py tests/test_activity_feed_widget.py
git commit -m "feat(activity-feed): mode chips with per-panel persistence"
```

---

## Task 9: Variable row variants (compact / expanded / card)

**Files:**
- Modify: `patchbai/widgets/activity_feed.py`
- Modify: `tests/test_activity_feed_widget.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_activity_feed_widget.py`:

```python
@pytest.mark.asyncio
async def test_card_variant_used_for_agent_ask(tmp_path):
    app = _build_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        from patchbai.events import AgentRequestedUserInput
        app.event_bus.publish(AgentRequestedUserInput(
            agent_id="bot", question="ok?", request_id="r1",
        ))
        await pilot.pause()
        from patchbai.widgets.activity_feed import ActivityFeed, _ActivityRow
        feed = app.query(ActivityFeed).first()
        rows = list(feed.query(_ActivityRow))
        ask_row = next(r for r in rows if r.entry.kind == "agent.ask")
        assert ask_row.has_class("-variant-card")


@pytest.mark.asyncio
async def test_expanded_variant_used_for_agent_message(tmp_path):
    app = _build_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        from patchbai.events import AgentMessageAppended
        app.event_bus.publish(AgentMessageAppended(
            agent_id="bot", role="assistant", text="hi",
        ))
        await pilot.pause()
        from patchbai.widgets.activity_feed import ActivityFeed, _ActivityRow
        feed = app.query(ActivityFeed).first()
        rows = list(feed.query(_ActivityRow))
        msg_row = next(r for r in rows if r.entry.kind == "agent.message")
        assert msg_row.has_class("-variant-expanded")


@pytest.mark.asyncio
async def test_compact_variant_used_for_tab_added(tmp_path):
    app = _build_app(tmp_path)
    app.event_bus.publish(TabAdded(tab_id="t1", title="Files"))
    async with app.run_test() as pilot:
        await pilot.pause()
        from patchbai.widgets.activity_feed import ActivityFeed, _ActivityRow
        feed = app.query(ActivityFeed).first()
        rows = list(feed.query(_ActivityRow))
        tab_row = next(r for r in rows if r.entry.kind == "tab.added" and r.entry.tab_id == "t1")
        assert tab_row.has_class("-variant-compact")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_activity_feed_widget.py -v`
Expected: AssertionErrors on the variant class checks.

- [ ] **Step 3: Implement variants**

In `patchbai/widgets/activity_feed.py`, add a variant lookup table near the top (after `_MODE_KINDS`):

```python
_VARIANT: dict[str, str] = {
    # Compact: routine signals.
    ActivityKind.TAB_ADDED: "compact",
    ActivityKind.TAB_CLOSED: "compact",
    ActivityKind.TAB_SWITCHED: "compact",
    ActivityKind.LAYOUT_APPLIED: "compact",
    ActivityKind.WORKSPACE_CWD: "compact",
    ActivityKind.AGENT_STATE: "compact",
    ActivityKind.AGENT_ARCHIVE: "compact",
    ActivityKind.FILE_SELECTED: "compact",
    ActivityKind.AGENT_TOOL: "compact",
    ActivityKind.ORCH_SESSION: "compact",

    # Expanded: carries a body worth reading.
    ActivityKind.ORCH_USER: "expanded",
    ActivityKind.ORCH_REPLY: "expanded",
    ActivityKind.AGENT_MESSAGE: "expanded",
    ActivityKind.AGENT_NOTIFY: "expanded",
    ActivityKind.AGENT_SPAWNED: "expanded",

    # Card: needs attention.
    ActivityKind.AGENT_ASK: "card",
    ActivityKind.LAYOUT_FAILED: "card",
    # AGENT_DONE: "compact" by default; ERROR overrides to "card" — handled below.
    ActivityKind.AGENT_DONE: "compact",
}


def _variant_for(entry: ActivityEntry) -> str:
    """Pick the variant for an entry. Most kinds map statically via _VARIANT;
    agent.done escalates to 'card' when the underlying state is ERROR."""
    if entry.kind == ActivityKind.AGENT_DONE:
        from patchbai.events import AgentStateChanged
        from patchbai.agents.state import AgentState
        raw = entry.raw
        if isinstance(raw, AgentStateChanged) and raw.info.state == AgentState.ERROR:
            return "card"
        return "compact"
    return _VARIANT.get(entry.kind, "compact")
```

Replace `_ActivityRow` with a variant-aware version:

```python
class _ActivityRow(Static):
    """One feed row. Variant comes from `_variant_for(entry)`; CSS classes
    `-variant-compact|expanded|card` drive presentation."""

    DEFAULT_CSS = """
    _ActivityRow {
        height: auto;
        padding: 0 1;
    }
    _ActivityRow.-variant-card {
        border: round $warning;
        padding: 0 1;
        margin: 0 0 1 0;
    }
    """

    def __init__(self, entry: ActivityEntry) -> None:
        variant = _variant_for(entry)
        super().__init__(self._format(entry, variant))
        self.entry = entry
        self.add_class(f"-variant-{variant}")

    @staticmethod
    def _format(entry: ActivityEntry, variant: str) -> str:
        ts = entry.timestamp.strftime("%H:%M:%S")
        head = f"[{ts}] {entry.kind:<18} {entry.summary}"
        if variant == "compact" or not entry.detail:
            return head
        if variant == "expanded":
            return f"{head}\n            ↳ {entry.detail}"
        # card
        return f"{entry.kind} · {entry.summary}\n{entry.detail}"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_activity_feed_widget.py -v`
Expected: 7 widget tests pass.

- [ ] **Step 5: Commit**

```bash
git add patchbai/widgets/activity_feed.py tests/test_activity_feed_widget.py
git commit -m "feat(activity-feed): compact/expanded/card row variants"
```

---

## Task 10: Click-through navigation

**Files:**
- Modify: `patchbai/widgets/activity_feed.py`
- Modify: `patchbai/widgets/agent_table.py`
- Modify: `tests/test_activity_feed_widget.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_activity_feed_widget.py`:

```python
@pytest.mark.asyncio
async def test_clicking_agent_row_publishes_focus_request(tmp_path):
    app = _build_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        from patchbai.events import AgentMessageAppended, AgentFocusRequested
        seen: list[AgentFocusRequested] = []
        app.event_bus.subscribe(AgentFocusRequested, lambda e: seen.append(e))
        app.event_bus.publish(AgentMessageAppended(
            agent_id="bot", role="assistant", text="hi",
        ))
        await pilot.pause()
        from patchbai.widgets.activity_feed import ActivityFeed, _ActivityRow
        feed = app.query(ActivityFeed).first()
        msg_row = next(r for r in feed.query(_ActivityRow) if r.entry.kind == "agent.message")
        await pilot.click(msg_row)
        await pilot.pause()
        assert any(e.agent_id == "bot" for e in seen)


@pytest.mark.asyncio
async def test_clicking_layout_failed_calls_notify(tmp_path):
    app = _build_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        from patchbai.events import LayoutFailed
        notified: list[tuple[str, str]] = []
        # Wrap notify; Textual returns None and stores notifications internally,
        # but we just need to know it was called.
        original_notify = app.notify

        def _wrapped(message, **kwargs):
            notified.append((message, kwargs.get("severity", "")))
            return original_notify(message, **kwargs)

        app.notify = _wrapped  # type: ignore[assignment]
        app.event_bus.publish(LayoutFailed(error="boom", tab_id="t1"))
        await pilot.pause()
        from patchbai.widgets.activity_feed import ActivityFeed, _ActivityRow
        feed = app.query(ActivityFeed).first()
        row = next(r for r in feed.query(_ActivityRow) if r.entry.kind == "layout.failed")
        await pilot.click(row)
        await pilot.pause()
        assert any("boom" in m and sev == "error" for m, sev in notified)


@pytest.mark.asyncio
async def test_clicking_non_interactive_row_does_nothing(tmp_path):
    """tab.switched isn't in _CLICK_HANDLERS, so clicking shouldn't fire any
    AgentFocusRequested or notification."""
    app = _build_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        from patchbai.events import TabSwitched, AgentFocusRequested
        seen: list[AgentFocusRequested] = []
        app.event_bus.subscribe(AgentFocusRequested, lambda e: seen.append(e))
        app.event_bus.publish(TabSwitched(tab_id="t1", title="Files"))
        await pilot.pause()
        from patchbai.widgets.activity_feed import ActivityFeed, _ActivityRow
        # tab.switched is debug-only; mount a debug feed via prop is overkill —
        # this widget is in audit mode by default and tab.switched is filtered
        # out, so the click path is implicitly covered. Use tab.added instead,
        # which IS visible in audit mode but has a click handler (tab switch),
        # not an agent focus request.
        feed = app.query(ActivityFeed).first()
        # Find a kind that should NOT fire AgentFocusRequested when clicked.
        rows = list(feed.query(_ActivityRow))
        # workspace.cwd has no click handler.
        from patchbai.events import WorkspaceCwdChanged
        app.event_bus.publish(WorkspaceCwdChanged(cwd="/tmp"))
        await pilot.pause()
        cwd_row = next(r for r in feed.query(_ActivityRow) if r.entry.kind == "workspace.cwd")
        before = len(seen)
        await pilot.click(cwd_row)
        await pilot.pause()
        assert len(seen) == before  # no new focus requests
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_activity_feed_widget.py -v`
Expected: AssertionErrors — clicks have no effect yet.

- [ ] **Step 3: Implement click-through**

In `patchbai/widgets/activity_feed.py`, add the click handlers map near the top (after `_VARIANT`):

```python
from typing import Callable

from patchbai.events import AgentFocusRequested


def _click_agent(app, entry: ActivityEntry) -> None:
    if entry.agent_id is None:
        return
    app.event_bus.publish(AgentFocusRequested(agent_id=entry.agent_id))


def _click_layout_failed(app, entry: ActivityEntry) -> None:
    msg = entry.detail or "layout failed"
    app.notify(msg, severity="error")


def _click_tab_added(app, entry: ActivityEntry) -> None:
    if entry.tab_id is None:
        return
    from textual.widgets import TabbedContent
    try:
        tc = app.query_one("#app-tabs", TabbedContent)
    except Exception:
        return
    target = f"tab-{entry.tab_id}"
    # Best-effort: if the tab no longer exists, the assignment will raise.
    try:
        tc.active = target
    except Exception:
        pass


def _click_orch_session(app, entry: ActivityEntry) -> None:
    try:
        target = app.query("OrchestratorChat #orch-input").first()
    except Exception:
        return
    target.focus()


_CLICK_HANDLERS: dict[str, Callable[[object, ActivityEntry], None]] = {
    ActivityKind.AGENT_SPAWNED: _click_agent,
    ActivityKind.AGENT_STATE: _click_agent,
    ActivityKind.AGENT_DONE: _click_agent,
    ActivityKind.AGENT_MESSAGE: _click_agent,
    ActivityKind.AGENT_TOOL: _click_agent,
    ActivityKind.AGENT_ASK: _click_agent,
    ActivityKind.AGENT_NOTIFY: _click_agent,
    ActivityKind.AGENT_ARCHIVE: _click_agent,
    ActivityKind.LAYOUT_FAILED: _click_layout_failed,
    ActivityKind.TAB_ADDED: _click_tab_added,
    ActivityKind.ORCH_SESSION: _click_orch_session,
}
```

Update `_ActivityRow` to add a `-clickable` class for kinds with handlers, and handle clicks:

```python
class _ActivityRow(Static):
    DEFAULT_CSS = """
    _ActivityRow {
        height: auto;
        padding: 0 1;
    }
    _ActivityRow.-variant-card {
        border: round $warning;
        padding: 0 1;
        margin: 0 0 1 0;
    }
    _ActivityRow.-clickable:hover {
        background: $boost;
    }
    """

    def __init__(self, entry: ActivityEntry) -> None:
        variant = _variant_for(entry)
        super().__init__(self._format(entry, variant))
        self.entry = entry
        self.add_class(f"-variant-{variant}")
        if entry.kind in _CLICK_HANDLERS:
            self.add_class("-clickable")

    def on_click(self, event) -> None:
        handler = _CLICK_HANDLERS.get(self.entry.kind)
        if handler is None:
            return
        handler(self.app, self.entry)
        event.stop()
```

(The existing `ActivityFeed.on_click` chip handler still runs because it inspects `event.widget`; clicks on a row have already been stopped, so they don't bubble to the chip handler.)

In `patchbai/widgets/agent_table.py`, add the focus-request subscription. After the existing `_on_archive_changed` handler, add:

```python
    def _on_focus_requested(self, event: AgentFocusRequested) -> None:
        """Select the row matching event.agent_id and scroll it into view."""
        agent_id = event.agent_id
        if agent_id not in self._rows:
            return
        try:
            table = self.query_one(DataTable)
        except Exception:
            return
        for index, row_key in enumerate(table.rows.keys()):
            if str(row_key.value) == agent_id:
                table.move_cursor(row=index)
                table.scroll_to(0, index, animate=False)
                return
```

In `patchbai/widgets/agent_table.py`, add `AgentFocusRequested` to the existing `from patchbai.events import (...)` block at the top of the file.

Then in `AgentTable.on_mount`, append a new subscription right after the existing `AgentArchiveChanged` line:

```python
        self._unsubs.append(
            bus.subscribe(AgentFocusRequested, self._on_focus_requested)
        )
```

The existing `_unsubs` list is already cleaned up in `on_unmount`, so no further wiring is needed.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_activity_feed_widget.py tests/test_agent_table_widget.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add patchbai/widgets/activity_feed.py patchbai/widgets/agent_table.py tests/test_activity_feed_widget.py
git commit -m "feat(activity-feed): click-through navigation for agent/layout/tab/orch rows"
```

---

## Task 11: Auto-follow scrolling

**Files:**
- Modify: `patchbai/widgets/activity_feed.py`
- Modify: `tests/test_activity_feed_widget.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_activity_feed_widget.py`:

```python
@pytest.mark.asyncio
async def test_new_event_scrolls_to_bottom_when_at_bottom(tmp_path):
    app = _build_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        from patchbai.widgets.activity_feed import ActivityFeed
        from textual.containers import VerticalScroll
        feed = app.query(ActivityFeed).first()
        scroll = feed.query_one("#activity-rows", VerticalScroll)
        # Fill enough rows to make the scroll meaningful.
        for i in range(60):
            app.event_bus.publish(TabAdded(tab_id=f"t{i}", title=f"Tab {i}"))
        await pilot.pause()
        # Scroll should be at (or near) bottom.
        assert scroll.scroll_y == pytest.approx(scroll.max_scroll_y, abs=2)


@pytest.mark.asyncio
async def test_user_scroll_up_pauses_autofollow(tmp_path):
    app = _build_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        from patchbai.widgets.activity_feed import ActivityFeed
        from textual.containers import VerticalScroll
        feed = app.query(ActivityFeed).first()
        scroll = feed.query_one("#activity-rows", VerticalScroll)
        for i in range(60):
            app.event_bus.publish(TabAdded(tab_id=f"t{i}", title=f"Tab {i}"))
        await pilot.pause()
        # Move user scroll to the top.
        scroll.scroll_to(0, 0, animate=False)
        await pilot.pause()
        # Now publish another event.
        app.event_bus.publish(TabAdded(tab_id="late", title="Late"))
        await pilot.pause()
        # We should NOT have jumped to the bottom.
        assert scroll.scroll_y < scroll.max_scroll_y - 2
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_activity_feed_widget.py -v`
Expected: assertion failures — auto-follow not implemented.

- [ ] **Step 3: Implement auto-follow**

Update `ActivityFeed`:

```python
    def _is_at_bottom(self) -> bool:
        scroll = self.query_one("#activity-rows", VerticalScroll)
        # Treat anything within 2 cells of bottom as "at bottom" — accounts
        # for fractional scrolls and avoids edge-case desync.
        return scroll.max_scroll_y - scroll.scroll_y <= 2

    def _scroll_to_bottom_if_following(self) -> None:
        if self._is_at_bottom():
            scroll = self.query_one("#activity-rows", VerticalScroll)
            scroll.scroll_end(animate=False)

    def _on_logged(self, event: ActivityLogged) -> None:
        entry: ActivityEntry = event.entry  # type: ignore[assignment]
        if entry.kind not in _MODE_KINDS[self.mode]:
            return
        scroll = self.query_one("#activity-rows", VerticalScroll)
        was_following = self._is_at_bottom()
        scroll.mount(_ActivityRow(entry))
        if was_following:
            # call_after_refresh so the new row's height is included in
            # max_scroll_y before we jump.
            self.call_after_refresh(lambda: scroll.scroll_end(animate=False))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_activity_feed_widget.py -v`
Expected: all widget tests pass.

- [ ] **Step 5: Commit**

```bash
git add patchbai/widgets/activity_feed.py tests/test_activity_feed_widget.py
git commit -m "feat(activity-feed): auto-follow scroll with pause-on-scroll-up"
```

---

## Task 12: Migrate placeholders.py imports and clean up

**Files:**
- Delete: `patchbai/widgets/placeholders.py`
- Modify: 9 test files (see File Structure section)

- [ ] **Step 1: Update test imports**

For each of these files, replace:

```python
from patchbai.widgets.placeholders import ActivityFeed
```

with:

```python
from patchbai.widgets.activity_feed import ActivityFeed
```

Files to update:
- `tests/test_layout_engine_focus.py`
- `tests/test_layout_engine_titles.py`
- `tests/test_layout_engine_weakref.py`
- `tests/test_app_smoke.py`
- `tests/test_layout_engine_idempotent.py`
- `tests/test_layout_engine_splitter.py`
- `tests/test_orchestrator_tools_get_layout.py`
- `tests/test_layout_engine_tabs.py`
- `tests/test_layout_titles_resolver.py`

Verify no other imports remain:

Run: `grep -rn "patchbai.widgets.placeholders\|from .placeholders" patchbai/ tests/`
Expected: no output.

- [ ] **Step 2: Delete placeholders.py**

Run: `rm patchbai/widgets/placeholders.py`

- [ ] **Step 3: Run the entire test suite**

Run: `.venv/bin/pytest tests/ -q`
Expected: all green; row count higher than the pre-feature baseline by ~25–30 new tests.

- [ ] **Step 4: Manual smoke test**

Launch the app:

Run: `.venv/bin/python -m patchbai`

Verify:
- The Activity panel in the default dashboard renders an "Audit · Agents · Notifs · Debug" chip strip at the top.
- Sending a message in the orchestrator produces an `orch.user` then `orch.reply` row.
- Clicking the "Agents" chip reduces visible rows; the choice persists across an app restart.
- Clicking an agent row in any agents-mode view selects the matching AgentTable row.
- Using `ctrl+t` to add a tab makes a `tab.added` row appear; clicking it switches to that tab.
- Triggering a bad layout (e.g., orchestrator MCP `apply_layout` with garbage) produces a `layout.failed` card; clicking it shows a Textual toast.

If anything fails, do NOT commit — return to the relevant task and fix.

- [ ] **Step 5: Commit**

```bash
git add tests/test_layout_engine_focus.py tests/test_layout_engine_titles.py tests/test_layout_engine_weakref.py tests/test_app_smoke.py tests/test_layout_engine_idempotent.py tests/test_layout_engine_splitter.py tests/test_orchestrator_tools_get_layout.py tests/test_layout_engine_tabs.py tests/test_layout_titles_resolver.py
git rm patchbai/widgets/placeholders.py
git commit -m "refactor(widgets): drop placeholders.py; ActivityFeed lives in its own module"
```

---

## Final Verification

- [ ] Run the full suite one more time:

Run: `.venv/bin/pytest tests/ -q`
Expected: all green.

- [ ] Verify clean git status:

Run: `git status`
Expected: clean working tree on `worktree-activity-feed`.

- [ ] Inspect the commit history:

Run: `git log --oneline main..HEAD`
Expected: 12 commits, one per task, none amended.
