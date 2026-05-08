# Patchfeld Plan 4 — Layout + Config Mutability

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the entire UI agent-mutable at runtime. Orchestrator gets `set_layout` / `save_layout` / `load_layout` / `list_layouts` (named layout presets in `~/.config/patchfeld/layouts/`), `bind_key` / `unbind_key` / `list_bindings` (hot-reloaded keybindings persisted in `~/.config/patchfeld/config.toml`), `set_config` / `get_config` / `list_actions` (typed config + an enumerable action registry), and `open_history` (a modal listing every agent ever spawned in this cwd, re-using the existing `agents.json`). User-facing additions: `ctrl-l` opens a layout switcher modal, `ctrl-h` opens the history modal, the StatusBar reflects the current layout name. Plus four carry-overs from plan 3's final review: shared test fixtures, a public `AgentSession.queue_send`, periodic pruning of `OrchestratorSession._send_tasks`, and the long-overdue `spawn_agent` schema fix.

**Architecture:** `LayoutEngine.apply` gains atomic rollback (already mostly there) and snapshots the focused panel id across rebuilds so focus survives a `set_layout`. A no-op fast-path skips work entirely when the new spec is identical to the current one. `ConfigStore` reads/writes `config.toml` via `tomllib` + `tomli-w`. `ActionRegistry` enumerates bindable callables (`focus_orchestrator`, `cycle_focus`, `quit`, `load_layout(name)`, `open_history`, etc.). `bind_key` re-applies the App's keybindings live (Textual's `Binding` list is rebuildable on the fly via `app.refresh_bindings`). The orchestrator's `_SPECS` table grows from 7 to 18 tools; the dual-path refactor from plan 3 absorbs all of them with no per-tool boilerplate. History and Layout-switcher are `ModalScreen`s built from the same widget shape as plan 2's `TranscriptScreen`.

**Tech Stack:** Python 3.11+, Textual, pydantic v2, `claude-agent-sdk`, `tomli-w` (NEW: writing TOML; reading uses stdlib `tomllib`), pytest + pytest-asyncio.

**Non-goals for this plan (deferred to later plans):**
- Rich widget library: DiffViewer, FileTree, FileViewer, LogTail, Markdown, Notebook, Terminal/PTY (plan 5)
- Mode-C custom widgets — `register_custom_widget` exec sandbox (plan 6)
- Replacing `bypassPermissions` with a Textual approval modal (deferred — likely plan 5 or 6)
- True incremental widget reuse across `set_layout` calls (props-only updates without re-mount). Plan 4 still rebuilds widget trees on every non-no-op `set_layout` — it just preserves focus and provides an idempotent fast-path. Genuine widget reuse with `update_props` per widget is plan 5+ work.
- Peer-to-peer messaging between child agents (out of scope for v1)

---

## File Structure

```
patchfeld/
  layout/
    engine.py                   (MODIFY: idempotent fast-path; LayoutFailed event on build error; preserve focus)
  events.py                     (EXTEND: LayoutApplied, LayoutFailed)
  orchestrator/
    tools.py                    (EXTEND _SPECS by 11 tools; fix spawn_agent schema)
    session.py                  (MODIFY: prune .done() tasks from _send_tasks)
  agents/
    session.py                  (NEW public method: queue_send)
    manager.py                  (MODIFY: _on_direct_message uses queue_send instead of _idle_event)
  config.py                     (NEW: ConfigStore — tomllib read + tomli-w write)
  actions.py                    (NEW: ActionRegistry — name → callable + signature)
  persistence/
    layouts_store.py            (NEW: NamedLayoutsStore — read/write/list at ~/.config/patchfeld/layouts/)
  widgets/
    history_screen.py           (NEW: ModalScreen listing all agents; click → TranscriptScreen)
    layout_switcher.py          (NEW: ModalScreen listing named layouts; select → load_layout)
    chrome.py                   (MODIFY: StatusBar.set_layout_name wired through LayoutApplied event)
  app.py                        (MODIFY: load config on boot; ctrl-h / ctrl-l actions; refresh bindings hook)
tests/
  conftest.py                   (NEW: shared _ok_script() / _script(text) fixtures)
  test_agent_session_queue_send.py
  test_orchestrator_session_prune.py
  test_layout_engine_focus.py
  test_layout_engine_idempotent.py
  test_layouts_store.py
  test_orchestrator_tools_layout.py
  test_config_store.py
  test_action_registry.py
  test_orchestrator_tools_config.py
  test_history_screen.py
  test_layout_switcher.py
  test_app_smoke_plan4.py        (e2e: orchestrator binds a key via tools)
```

---

## Task 1 — `tests/conftest.py` shared fixtures

**Files:**
- Create: `tests/conftest.py`

The plan-3 final review flagged that `_ok_script() / _script(text) / _ok()` is duplicated across ~9 test files. We extract it to a single conftest fixture and refactor a couple of representative call sites to use it. New tests in this plan use the fixture; old tests continue to work with their inline helpers (no aggressive global rewrite — that's a separate cleanup pass).

- [ ] **Step 1: Implement `tests/conftest.py`**

```python
import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock


@pytest.fixture
def ok_script():
    """A factory returning a 2-message SDK script: one assistant TextBlock + a
    success ResultMessage. Reusable across tests; defaults to 'done'."""
    def _make(text: str = "done") -> list:
        return [
            AssistantMessage(content=[TextBlock(text=text)], model="fake-model"),
            ResultMessage(
                subtype="success",
                duration_ms=1,
                duration_api_ms=1,
                is_error=False,
                num_turns=1,
                session_id="fake",
                total_cost_usd=0.0,
                usage={"input_tokens": 1, "output_tokens": 1},
                result=text,
            ),
        ]
    return _make
```

- [ ] **Step 2: Verify the fixture is discoverable**

```bash
cd /Users/jimmy.mills/Developer/patchfeld && .venv/bin/pytest --fixtures 2>&1 | grep -A 2 ok_script
```

Expected: lists `ok_script` with the docstring.

```bash
.venv/bin/pytest -q
```

Expected: full suite still green (122 + 1 skipped — no behavioral change).

- [ ] **Step 3: Commit**

```bash
git add tests/conftest.py
git commit -m "test: shared ok_script fixture in conftest.py"
```

---

## Task 2 — `AgentSession.queue_send` public API

**Files:**
- Modify: `patchfeld/agents/session.py`
- Modify: `patchfeld/agents/manager.py`
- Test: `tests/test_agent_session_queue_send.py`

`AgentManager._on_direct_message` currently reaches into `session._idle_event.clear()` to defeat a wait_idle race. Replace with a public `AgentSession.queue_send(text)` method that returns the created task and eagerly clears the idle event from inside the session.

- [ ] **Step 1: Write the failing test**

Create `tests/test_agent_session_queue_send.py`:

```python
import pytest
from claude_agent_sdk import ClaudeAgentOptions

from patchfeld.agents.fake_sdk_adapter import FakeSDKAdapter
from patchfeld.agents.session import AgentSession
from patchfeld.agents.state import AgentInfo
from patchfeld.events import EventBus
from patchfeld.persistence.transcript_store import AgentTranscript


def _info() -> AgentInfo:
    return AgentInfo(id="a1", name="x", cwd="/tmp", started_at=100.0)


@pytest.mark.asyncio
async def test_queue_send_returns_a_task_that_completes(tmp_path, ok_script):
    bus = EventBus()
    adapter = FakeSDKAdapter(scripts=[ok_script("hi")])
    session = AgentSession(
        info=_info(),
        adapter=adapter,
        transcript=AgentTranscript(cwd=tmp_path, agent_id="a1"),
        bus=bus,
    )
    await session.start(options=ClaudeAgentOptions())

    task = session.queue_send("hello")
    assert not task.done(), "queue_send must return a not-yet-done task"
    await task
    await session.wait_idle()

    entries = session._transcript.read_all()
    assert any(e.role == "user" and e.text == "hello" for e in entries)


@pytest.mark.asyncio
async def test_queue_send_eagerly_clears_idle_event(tmp_path, ok_script):
    """wait_idle() right after queue_send() must block until the task completes."""
    bus = EventBus()
    adapter = FakeSDKAdapter(scripts=[ok_script("hi")])
    session = AgentSession(
        info=_info(),
        adapter=adapter,
        transcript=AgentTranscript(cwd=tmp_path, agent_id="a1"),
        bus=bus,
    )
    await session.start(options=ClaudeAgentOptions())

    session.queue_send("hello")
    # If queue_send didn't eagerly clear the idle event, wait_idle could return
    # before the queued send has even acquired the send lock.
    await session.wait_idle()

    entries = session._transcript.read_all()
    assert any(e.role == "user" for e in entries), "user message must have been recorded"
```

- [ ] **Step 2: Run and confirm they fail**

```bash
.venv/bin/pytest tests/test_agent_session_queue_send.py -v
```

Expected: AttributeError on `queue_send`.

- [ ] **Step 3: Add `queue_send` to `patchfeld/agents/session.py`**

Insert this method on the `AgentSession` class, immediately after `send`:

```python
    def queue_send(self, prompt: str) -> "asyncio.Task":
        """Schedule a send() on the running event loop and return the Task.

        Eagerly clears `_idle_event` synchronously so a subsequent wait_idle()
        in the same task will correctly block until the send completes —
        without it, wait_idle could return before the send task acquires the
        send lock.
        """
        self._idle_event.clear()
        return asyncio.create_task(self.send(prompt))
```

- [ ] **Step 4: Update `AgentManager._on_direct_message` to use `queue_send`**

In `patchfeld/agents/manager.py`, replace `_on_direct_message` with:

```python
    def _on_direct_message(self, event: DirectMessageToAgent) -> None:
        session = self._sessions.get(event.agent_id)
        if session is None:
            return  # silently ignore stale messages for dead agents
        session.queue_send(event.text)
```

(Drop the inline `import asyncio as _asyncio` and the `_idle_event.clear()` workaround.)

- [ ] **Step 5: Run all tests**

```bash
.venv/bin/pytest tests/test_agent_session_queue_send.py tests/test_direct_message_to_agent.py -v
.venv/bin/pytest -q
```

Expected: 2 new pass; existing direct-message tests still pass; full suite green (124 + 1 skipped).

- [ ] **Step 6: Commit**

```bash
git add patchfeld/agents/session.py patchfeld/agents/manager.py tests/test_agent_session_queue_send.py
git commit -m "feat(agents): public AgentSession.queue_send; remove _idle_event leak in manager"
```

---

## Task 3 — Prune `OrchestratorSession._send_tasks`

**Files:**
- Modify: `patchfeld/orchestrator/session.py`
- Test: `tests/test_orchestrator_session_prune.py`

`_send_tasks` accumulates indefinitely between `wait_idle()` calls; the plan-3 reviewer flagged this as a leak in long-running orchestrators. Prune `.done()` entries every time a new send is queued.

- [ ] **Step 1: Write the failing test**

Create `tests/test_orchestrator_session_prune.py`:

```python
import asyncio

import pytest

from patchfeld.agents.fake_sdk_adapter import FakeSDKAdapter
from patchfeld.agents.manager import AgentManager
from patchfeld.events import EventBus, UserMessageToOrchestrator
from patchfeld.orchestrator.session import OrchestratorSession


@pytest.mark.asyncio
async def test_send_tasks_does_not_grow_unboundedly(tmp_path, ok_script):
    bus = EventBus()
    manager = AgentManager(
        cwd=tmp_path,
        bus=bus,
        adapter_factory=lambda: FakeSDKAdapter(scripts=[ok_script()]),
    )
    scripts = [ok_script(f"reply {i}") for i in range(5)]
    session = OrchestratorSession(
        cwd=tmp_path,
        bus=bus,
        manager=manager,
        adapter=FakeSDKAdapter(scripts=scripts),
    )
    await session.start()

    for i in range(5):
        bus.publish(UserMessageToOrchestrator(f"msg {i}"))
        await session.wait_idle()
        # Each iteration should leave at most one not-yet-done task (the one
        # currently being awaited inside wait_idle).
        live = [t for t in session._send_tasks if not t.done()]
        assert len(live) <= 1, f"send_tasks accumulated: {live}"

    # After the final wait_idle, no live tasks should remain.
    live = [t for t in session._send_tasks if not t.done()]
    assert live == []
```

- [ ] **Step 2: Run and confirm it fails**

```bash
.venv/bin/pytest tests/test_orchestrator_session_prune.py -v
```

Expected: failure (the list will have ~5 done tasks lingering, and the count check beyond the first iteration would still pass since only one is "live" — actually re-verify: the test counts NOT-done tasks, which should always be ≤1. The post-loop assertion `live == []` should pass too. So this test may pass without changes.).

If the test PASSES without code changes, that's expected — the inner lock means there's no time when multiple sends are in-flight. Skip Step 3 (the prune is for memory hygiene, not correctness). Verify the test passes, then add a stronger assertion that pins the `len(session._send_tasks)` total (including done ones) is bounded.

Replace the assertion in the loop with:

```python
        # Done tasks must be pruned on each new send.
        assert len(session._send_tasks) <= 2, (
            f"send_tasks grew to {len(session._send_tasks)} — pruning failed"
        )
```

This will fail because the current implementation never prunes — every queued task stays in the list forever.

- [ ] **Step 3: Modify `_on_user_message` in `patchfeld/orchestrator/session.py`**

Replace the `_on_user_message` method with:

```python
    def _on_user_message(self, event: UserMessageToOrchestrator) -> None:
        # Prune any tasks that have already completed before adding a new one.
        self._send_tasks = [t for t in self._send_tasks if not t.done()]
        # The bus is sync — schedule the async send on the running loop.
        task = self._inner.queue_send(event.text)
        self._send_tasks.append(task)
```

(This also switches from `asyncio.create_task(self._inner.send(...))` to the new `_inner.queue_send(...)` from Task 2 — both keep the `task` object trackable, but `queue_send` adds the eager idle-event clear which avoids the race that Task 5 of plan 3 worked around.)

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest tests/test_orchestrator_session_prune.py tests/test_orchestrator_session_serializes.py -v
.venv/bin/pytest -q
```

Expected: new prune test passes; existing serialize test still passes; full suite green (125 + 1 skipped).

- [ ] **Step 5: Commit**

```bash
git add patchfeld/orchestrator/session.py tests/test_orchestrator_session_prune.py
git commit -m "fix(orchestrator): prune done _send_tasks; use queue_send"
```

---

## Task 4 — Fix `spawn_agent` schema (carry-over from plan 2)

**Files:**
- Modify: `patchfeld/orchestrator/tools.py`

The `spawn_agent` handler reads `args.get("cwd")` and `args.get("allowed_tools")` but the JSON schema only declares `name` and `prompt`. Add the optional fields to the schema so the orchestrator AI knows about them, AND so the SDK actually passes them through.

- [ ] **Step 1: Modify `_SPECS` entry for `spawn_agent`**

In `patchfeld/orchestrator/tools.py`, find the `_ToolSpec(name="spawn_agent", ...)` entry and replace its `input_schema` and `description`:

```python
    _ToolSpec(
        name="spawn_agent",
        description=(
            "Spawn a new Claude Code child agent. `name` is a short label "
            "for the table; `prompt` is the initial task. Optional `cwd` "
            "overrides the working directory; optional `allowed_tools` is a "
            "list of tool names to whitelist for this child (defaults to "
            "inheriting the user's settings.json)."
        ),
        input_schema={
            "name": str,
            "prompt": str,
            "cwd": str,
            "allowed_tools": list,
        },
        build=_spawn_handler,
    ),
```

The handler already gracefully handles missing `cwd`/`allowed_tools` via `args.get(...)`.

If the SDK's `tool` decorator rejects required-only schemas (i.e. all fields are required by default), the spawn_agent call from the AI side will start failing for missing `cwd` / `allowed_tools`. STOP and report — we may need to mark them optional via a tagged schema syntax. The simplest workaround is to keep the schema as `{name, prompt}` but document the optional fields in the description so the AI still passes them and the handler still picks them up via `args.get`. Use whichever path actually works; explain what you did in the report.

- [ ] **Step 2: Run existing spawn tests**

```bash
.venv/bin/pytest tests/test_orchestrator_tools.py tests/test_app_smoke_plan2.py -v
.venv/bin/pytest -q
```

Expected: existing tests still pass; full suite green.

- [ ] **Step 3: Commit**

```bash
git add patchfeld/orchestrator/tools.py
git commit -m "fix(orchestrator): expose cwd + allowed_tools in spawn_agent schema"
```

---

## Task 5 — `LayoutEngine.apply` preserves focus across rebuilds

**Files:**
- Modify: `patchfeld/layout/engine.py`
- Test: `tests/test_layout_engine_focus.py`

When `set_layout` causes a rebuild, the user's focused panel id should survive. Snapshot focus before `remove_children`, restore after `mount_all`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_layout_engine_focus.py`:

```python
from pathlib import Path

import pytest
from textual.app import App
from textual.containers import Container

from patchfeld.layout.defaults import dashboard_layout
from patchfeld.layout.engine import apply as apply_layout
from patchfeld.layout.registry import WidgetRegistry
from patchfeld.layout.spec import LayoutSpec
from patchfeld.widgets.orchestrator_chat import OrchestratorChat
from patchfeld.widgets.placeholders import ActivityFeed
from patchfeld.widgets.agent_table import AgentTable


def _registry() -> WidgetRegistry:
    reg = WidgetRegistry()
    reg.register("OrchestratorChat", OrchestratorChat)
    reg.register("AgentTable", AgentTable)
    reg.register("ActivityFeed", ActivityFeed)
    return reg


class _HostApp(App):
    def compose(self):
        yield Container(id="panel-area")


def _spec_with_focus(focus: str) -> LayoutSpec:
    spec = dashboard_layout()
    return LayoutSpec.model_validate({**spec.model_dump(mode="json"), "focus": focus})


@pytest.mark.asyncio
async def test_apply_preserves_focused_panel_id_across_rebuilds(tmp_path: Path):
    app = _HostApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        area = app.query_one("#panel-area", Container)
        registry = _registry()

        # First apply: focus orchestrator.
        await apply_layout(area, _spec_with_focus("orch"), registry)
        await pilot.pause()
        focused_id = app.focused.id if app.focused else None
        assert focused_id == "panel-orch"

        # Second apply: same layout but no `focus` field — focus should
        # survive because the panel id "orch" is still present.
        spec_no_focus = LayoutSpec.model_validate({
            **dashboard_layout().model_dump(mode="json"),
            "focus": None,
        })
        await apply_layout(area, spec_no_focus, registry)
        await pilot.pause()
        focused_id_after = app.focused.id if app.focused else None
        assert focused_id_after == "panel-orch", (
            f"focus should survive rebuild; got {focused_id_after}"
        )
```

- [ ] **Step 2: Run and confirm failure**

```bash
.venv/bin/pytest tests/test_layout_engine_focus.py -v
```

Expected: failure on the second assertion — focus is lost across rebuild because `apply` only honors `spec.focus` and the second spec has `focus=None`.

- [ ] **Step 3: Modify `apply` in `patchfeld/layout/engine.py`**

Replace the `apply` function with:

```python
async def apply(container: TxContainer, spec: LayoutSpec, registry) -> None:
    """Replace `container`'s children with widgets built from `spec.layout`.

    Preserves focus across rebuilds: if no explicit `spec.focus` is set, the
    currently-focused panel id (if any) is restored after the rebuild —
    provided the panel still exists in the new spec.

    Atomic: builds the new tree fully (registry lookups + instantiation)
    before touching the container. If anything raises, nothing is mounted.
    """
    new_children = [_build(spec.layout, registry)]

    # Snapshot the currently-focused panel id (e.g., "orch" from "panel-orch").
    snapshot_focus_id: str | None = None
    try:
        app = container.app
        focused = app.focused
        if focused is not None and focused.id and focused.id.startswith("panel-"):
            snapshot_focus_id = focused.id[len("panel-"):]
    except Exception:
        pass

    await container.remove_children()
    await container.mount_all(new_children)

    target = spec.focus or snapshot_focus_id
    if target:
        try:
            container.query_one(f"#panel-{target}").focus()
        except Exception:
            pass
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest tests/test_layout_engine_focus.py tests/test_app_smoke.py -v
.venv/bin/pytest -q
```

Expected: new test passes; existing app smoke tests still pass.

- [ ] **Step 5: Commit**

```bash
git add patchfeld/layout/engine.py tests/test_layout_engine_focus.py
git commit -m "feat(layout): apply preserves focused panel id across rebuilds"
```

---

## Task 6 — `LayoutApplied` and `LayoutFailed` events; idempotent fast-path

**Files:**
- Modify: `patchfeld/events.py`
- Modify: `patchfeld/layout/engine.py`
- Test: `tests/test_layout_engine_idempotent.py`

Two more pieces fall out of `apply` becoming a runtime tool:
1. Publish `LayoutApplied(spec)` after successful mount, `LayoutFailed(error)` on build error. Lets the StatusBar (Task 16) and the orchestrator react.
2. If the new spec equals the currently-mounted spec exactly, skip the rebuild entirely — that's the spec's "no scroll-jump for re-emits" promise for the props-equal case.

- [ ] **Step 1: Append two events to `patchfeld/events.py`**

```python
@dataclass(frozen=True)
class LayoutApplied:
    """The LayoutEngine successfully applied a new spec."""
    spec: "LayoutSpec"
    layout_name: str | None = None  # if loaded by name; else None


@dataclass(frozen=True)
class LayoutFailed:
    """The LayoutEngine rejected a spec at build time."""
    error: str
```

Add the necessary import at the top of `events.py`:

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from patchfeld.layout.spec import LayoutSpec
```

(Using TYPE_CHECKING avoids a circular import: `patchfeld.layout.spec` doesn't import events, so this is safe.)

- [ ] **Step 2: Write the failing test**

Create `tests/test_layout_engine_idempotent.py`:

```python
import pytest
from textual.app import App
from textual.containers import Container

from patchfeld.events import EventBus, LayoutApplied, LayoutFailed
from patchfeld.layout.defaults import dashboard_layout
from patchfeld.layout.engine import apply as apply_layout
from patchfeld.layout.registry import WidgetRegistry
from patchfeld.layout.spec import LayoutSpec
from patchfeld.widgets.agent_table import AgentTable
from patchfeld.widgets.orchestrator_chat import OrchestratorChat
from patchfeld.widgets.placeholders import ActivityFeed


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
async def test_apply_publishes_layout_applied_event():
    bus = EventBus()
    applied: list[LayoutApplied] = []
    bus.subscribe(LayoutApplied, applied.append)

    app = _HostApp(bus)
    async with app.run_test() as pilot:
        await pilot.pause()
        area = app.query_one("#panel-area", Container)
        await apply_layout(area, dashboard_layout(), _registry())
        await pilot.pause()

    assert len(applied) == 1
    assert applied[0].spec == dashboard_layout()


@pytest.mark.asyncio
async def test_apply_idempotent_when_spec_unchanged_no_remount():
    bus = EventBus()
    applied: list[LayoutApplied] = []
    bus.subscribe(LayoutApplied, applied.append)

    app = _HostApp(bus)
    async with app.run_test() as pilot:
        await pilot.pause()
        area = app.query_one("#panel-area", Container)

        await apply_layout(area, dashboard_layout(), _registry())
        await pilot.pause()
        first_chat = app.query_one(OrchestratorChat)

        # Re-apply identical spec: no remount.
        await apply_layout(area, dashboard_layout(), _registry())
        await pilot.pause()
        second_chat = app.query_one(OrchestratorChat)

    assert first_chat is second_chat, "identical spec must skip the rebuild"
    # Two LayoutApplied events still fire — the apply is idempotent in effect,
    # not in side-effect-suppression. Subscribers can dedupe if needed.
    assert len(applied) == 2


@pytest.mark.asyncio
async def test_apply_publishes_layout_failed_on_unknown_widget():
    bus = EventBus()
    failed: list[LayoutFailed] = []
    bus.subscribe(LayoutFailed, failed.append)

    app = _HostApp(bus)
    async with app.run_test() as pilot:
        await pilot.pause()
        area = app.query_one("#panel-area", Container)
        registry = _registry()

        bad_spec = LayoutSpec.model_validate({
            "version": 1,
            "layout": {
                "type": "horizontal",
                "children": [
                    {"id": "orch", "widget": "OrchestratorChat"},
                    {"id": "x", "widget": "DoesNotExist"},
                ],
            },
        })

        with pytest.raises(Exception):
            await apply_layout(area, bad_spec, registry)
        await pilot.pause()

    assert len(failed) == 1
    assert "DoesNotExist" in failed[0].error
```

- [ ] **Step 3: Run and confirm failures**

```bash
.venv/bin/pytest tests/test_layout_engine_idempotent.py -v
```

Expected: 3 failures (no LayoutApplied/LayoutFailed events fire; no fast-path on identical spec).

- [ ] **Step 4: Modify `patchfeld/layout/engine.py`**

Replace the `apply` function with:

```python
# Track the most recent applied spec per container (used for fast-path).
_last_applied_spec: dict[int, LayoutSpec] = {}


async def apply(container: TxContainer, spec: LayoutSpec, registry,
                *, layout_name: str | None = None) -> None:
    """Replace `container`'s children with widgets built from `spec.layout`.

    Behavior:
    - **Idempotent fast-path:** if `spec` equals the last-applied spec for this
      container, skip the rebuild entirely. A `LayoutApplied` event is still
      fired so subscribers can refresh anything spec-derived (e.g., the
      StatusBar's layout name).
    - **Atomic build:** the new widget tree is fully constructed before any
      existing child is removed. If `_build` raises (e.g., UnknownWidgetError),
      a `LayoutFailed(error=str(exc))` event is published and the previous
      mounted layout stays untouched. The exception is re-raised.
    - **Focus preservation:** if `spec.focus` is None and a panel is currently
      focused, restore that panel's id after the rebuild (provided the panel
      survives in the new spec).
    """
    bus = getattr(container.app, "event_bus", None)

    # Fast-path: same spec → no rebuild.
    if _last_applied_spec.get(id(container)) == spec:
        if bus is not None:
            bus.publish(LayoutApplied(spec=spec, layout_name=layout_name))
        return

    # Atomic build (raises before any mount changes).
    try:
        new_children = [_build(spec.layout, registry)]
    except Exception as e:
        if bus is not None:
            bus.publish(LayoutFailed(error=str(e)))
        raise

    # Snapshot focus.
    snapshot_focus_id: str | None = None
    try:
        focused = container.app.focused
        if focused is not None and focused.id and focused.id.startswith("panel-"):
            snapshot_focus_id = focused.id[len("panel-"):]
    except Exception:
        pass

    await container.remove_children()
    await container.mount_all(new_children)

    target = spec.focus or snapshot_focus_id
    if target:
        try:
            container.query_one(f"#panel-{target}").focus()
        except Exception:
            pass

    _last_applied_spec[id(container)] = spec
    if bus is not None:
        bus.publish(LayoutApplied(spec=spec, layout_name=layout_name))
```

The two new imports at the top of the existing imports section:

```python
from patchfeld.events import LayoutApplied, LayoutFailed
```

- [ ] **Step 5: Run tests**

```bash
.venv/bin/pytest tests/test_layout_engine_idempotent.py tests/test_layout_engine_focus.py tests/test_app_smoke.py -v
.venv/bin/pytest -q
```

Expected: 3 new pass; existing app smoke tests still pass; full suite green (128 + 1 skipped).

- [ ] **Step 6: Commit**

```bash
git add patchfeld/events.py patchfeld/layout/engine.py tests/test_layout_engine_idempotent.py
git commit -m "feat(layout): LayoutApplied/Failed events; idempotent fast-path on identical spec"
```

---

## Task 7 — Named-layouts persistence

**Files:**
- Create: `patchfeld/persistence/layouts_store.py`
- Test: `tests/test_layouts_store.py`

Named layouts live in `~/.config/patchfeld/layouts/<name>.json`. The `NamedLayoutsStore` reads/writes them, lists them, and validates them via `LayoutSpec`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_layouts_store.py`:

```python
from pathlib import Path

import pytest

from patchfeld.layout.spec import LayoutSpec
from patchfeld.persistence.layouts_store import NamedLayoutsStore


def _spec() -> LayoutSpec:
    return LayoutSpec.model_validate({
        "version": 1,
        "layout": {"id": "orch", "widget": "OrchestratorChat"},
    })


def test_save_and_load_round_trip(tmp_path: Path):
    store = NamedLayoutsStore(global_dir=tmp_path)
    store.save("triage", _spec())
    assert store.load("triage") == _spec()


def test_load_missing_returns_none(tmp_path: Path):
    store = NamedLayoutsStore(global_dir=tmp_path)
    assert store.load("nope") is None


def test_save_creates_layouts_dir(tmp_path: Path):
    store = NamedLayoutsStore(global_dir=tmp_path)
    store.save("triage", _spec())
    assert (tmp_path / "layouts" / "triage.json").exists()


def test_list_returns_saved_names_sorted(tmp_path: Path):
    store = NamedLayoutsStore(global_dir=tmp_path)
    store.save("triage", _spec())
    store.save("deep-dive", _spec())
    store.save("review", _spec())
    assert store.list() == ["deep-dive", "review", "triage"]


def test_load_invalid_file_returns_none(tmp_path: Path):
    layouts = tmp_path / "layouts"
    layouts.mkdir()
    (layouts / "broken.json").write_text("not json {{")
    store = NamedLayoutsStore(global_dir=tmp_path)
    assert store.load("broken") is None


def test_save_rejects_invalid_name(tmp_path: Path):
    store = NamedLayoutsStore(global_dir=tmp_path)
    with pytest.raises(ValueError):
        store.save("../escape", _spec())
    with pytest.raises(ValueError):
        store.save("name/with/slashes", _spec())
    with pytest.raises(ValueError):
        store.save("", _spec())
```

- [ ] **Step 2: Run and confirm they fail**

```bash
.venv/bin/pytest tests/test_layouts_store.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement `patchfeld/persistence/layouts_store.py`**

```python
import json
import logging
import re
from pathlib import Path

from patchfeld.layout.spec import LayoutSpec
from patchfeld.persistence.atomic import write_json_atomic

log = logging.getLogger(__name__)

_VALID_NAME = re.compile(r"^[A-Za-z0-9_\-]+$")


class NamedLayoutsStore:
    """Read/write named LayoutSpecs at <global_dir>/layouts/<name>.json."""

    def __init__(self, global_dir: Path) -> None:
        self._dir = Path(global_dir) / "layouts"

    def save(self, name: str, spec: LayoutSpec) -> None:
        if not name or not _VALID_NAME.match(name):
            raise ValueError(
                f"layout name must match {_VALID_NAME.pattern!r}, got {name!r}"
            )
        write_json_atomic(self._dir / f"{name}.json", spec.model_dump(mode="json"))

    def load(self, name: str) -> LayoutSpec | None:
        path = self._dir / f"{name}.json"
        if not path.exists():
            return None
        try:
            return LayoutSpec.model_validate(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            log.exception("Failed to load named layout %r", name)
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

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest tests/test_layouts_store.py -v
.venv/bin/pytest -q
```

Expected: 6 new pass; full suite green (134 + 1 skipped).

- [ ] **Step 5: Commit**

```bash
git add patchfeld/persistence/layouts_store.py tests/test_layouts_store.py
git commit -m "feat(persistence): NamedLayoutsStore for save/load/list of named LayoutSpecs"
```

---

## Task 8 — Orchestrator MCP tools: `set_layout`, `save_layout`, `load_layout`, `list_layouts`

**Files:**
- Modify: `patchfeld/orchestrator/tools.py`
- Test: `tests/test_orchestrator_tools_layout.py`

Four new entries in `_SPECS`. They need access to the live App (to call `apply_layout` against the real container) and to a `NamedLayoutsStore`. Both come in via the `AgentManager` — extend it to optionally hold an `App` reference and a `NamedLayoutsStore`.

Hmm wait — `AgentManager` shouldn't hold UI state. Cleaner: the orchestrator session owns these references and passes them to a separate "layout tools" builder. Add a new `build_layout_tools(layout_engine_apply, store)` that returns its own `_ToolSpec` list, and merge into `_SPECS` at server-build time.

Actually simplest: the layout tools need a callable that applies a spec and a store. Both are stateless from the AgentManager's point of view. We pass them as additional kwargs to `build_orchestrator_tools` and `build_orchestrator_mcp_server`. Default to None — if absent, the layout tools are not registered.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_orchestrator_tools_layout.py`:

```python
import json
from pathlib import Path

import pytest

from patchfeld.agents.fake_sdk_adapter import FakeSDKAdapter
from patchfeld.agents.manager import AgentManager
from patchfeld.events import EventBus
from patchfeld.layout.defaults import dashboard_layout
from patchfeld.layout.spec import LayoutSpec
from patchfeld.orchestrator.tools import build_orchestrator_tools
from patchfeld.persistence.layouts_store import NamedLayoutsStore


def _make_manager(tmp_path, ok_script):
    return AgentManager(
        cwd=tmp_path,
        bus=EventBus(),
        adapter_factory=lambda: FakeSDKAdapter(scripts=[ok_script()]),
    )


@pytest.mark.asyncio
async def test_set_layout_calls_the_apply_callable(tmp_path, ok_script):
    manager = _make_manager(tmp_path, ok_script)
    store = NamedLayoutsStore(global_dir=tmp_path)
    applied: list[LayoutSpec] = []

    async def apply_callable(spec: LayoutSpec, *, layout_name: str | None = None) -> None:
        applied.append(spec)

    tools = build_orchestrator_tools(
        manager, apply_layout=apply_callable, layouts_store=store
    )
    # Tuple now has 7 + 4 = 11 entries; set_layout is at the end.
    set_layout = tools[7]

    spec_dict = dashboard_layout().model_dump(mode="json")
    out = await set_layout({"spec": spec_dict})
    assert "applied" in out["content"][0]["text"].lower()
    assert applied == [dashboard_layout()]


@pytest.mark.asyncio
async def test_save_layout_then_load_round_trips(tmp_path, ok_script):
    manager = _make_manager(tmp_path, ok_script)
    store = NamedLayoutsStore(global_dir=tmp_path)

    async def apply_callable(spec, *, layout_name=None):
        pass

    tools = build_orchestrator_tools(
        manager, apply_layout=apply_callable, layouts_store=store
    )
    # Tuple: spawn, list, read, send, interrupt, kill, respond,
    #        set_layout, save_layout, load_layout, list_layouts.
    save_layout = tools[8]
    load_layout = tools[9]
    list_layouts = tools[10]

    spec = dashboard_layout()
    # Save
    out_save = await save_layout({"name": "triage", "spec": spec.model_dump(mode="json")})
    assert "saved" in out_save["content"][0]["text"].lower()
    # List
    out_list = await list_layouts({})
    text = out_list["content"][0]["text"]
    assert "triage" in text
    # Load
    out_load = await load_layout({"name": "triage"})
    assert "loaded" in out_load["content"][0]["text"].lower()


@pytest.mark.asyncio
async def test_set_layout_with_invalid_spec_returns_error_text(tmp_path, ok_script):
    manager = _make_manager(tmp_path, ok_script)
    store = NamedLayoutsStore(global_dir=tmp_path)

    async def apply_callable(spec, *, layout_name=None):
        pass

    tools = build_orchestrator_tools(
        manager, apply_layout=apply_callable, layouts_store=store
    )
    set_layout = tools[7]

    # Missing OrchestratorChat invariant.
    bad = {"version": 1, "layout": {"id": "x", "widget": "AgentTable"}}
    out = await set_layout({"spec": bad})
    assert "error" in out["content"][0]["text"].lower() or "invalid" in out["content"][0]["text"].lower()


@pytest.mark.asyncio
async def test_load_layout_missing_returns_error_text(tmp_path, ok_script):
    manager = _make_manager(tmp_path, ok_script)
    store = NamedLayoutsStore(global_dir=tmp_path)

    async def apply_callable(spec, *, layout_name=None):
        pass

    tools = build_orchestrator_tools(
        manager, apply_layout=apply_callable, layouts_store=store
    )
    load_layout = tools[9]

    out = await load_layout({"name": "nonexistent"})
    text = out["content"][0]["text"].lower()
    assert "not found" in text or "no such layout" in text or "unknown" in text
```

- [ ] **Step 2: Run and confirm they fail**

```bash
.venv/bin/pytest tests/test_orchestrator_tools_layout.py -v
```

Expected: failures (the new tools don't exist; `build_orchestrator_tools` doesn't accept `apply_layout` / `layouts_store` kwargs).

- [ ] **Step 3: Modify `patchfeld/orchestrator/tools.py`**

Add the new handler builders ABOVE `_SPECS`:

```python
from typing import Optional, Awaitable as _Awaitable

from patchfeld.layout.spec import LayoutSpec
from patchfeld.persistence.layouts_store import NamedLayoutsStore


def _set_layout_handler(apply_layout):
    async def set_layout_tool(args: dict) -> dict:
        try:
            spec = LayoutSpec.model_validate(args["spec"])
        except Exception as e:
            return {"content": [{"type": "text", "text": f"Invalid LayoutSpec: {e}"}]}
        try:
            await apply_layout(spec)
        except Exception as e:
            return {"content": [{"type": "text", "text": f"Apply error: {e}"}]}
        return {"content": [{"type": "text", "text": "Layout applied."}]}
    return set_layout_tool


def _save_layout_handler(layouts_store: NamedLayoutsStore):
    async def save_layout_tool(args: dict) -> dict:
        name = args["name"]
        try:
            spec = LayoutSpec.model_validate(args["spec"])
        except Exception as e:
            return {"content": [{"type": "text", "text": f"Invalid LayoutSpec: {e}"}]}
        try:
            layouts_store.save(name, spec)
        except ValueError as e:
            return {"content": [{"type": "text", "text": f"Invalid layout name: {e}"}]}
        return {"content": [{"type": "text", "text": f"Saved layout {name!r}."}]}
    return save_layout_tool


def _load_layout_handler(apply_layout, layouts_store: NamedLayoutsStore):
    async def load_layout_tool(args: dict) -> dict:
        name = args["name"]
        spec = layouts_store.load(name)
        if spec is None:
            return {"content": [{"type": "text", "text": f"Layout not found: {name}"}]}
        try:
            await apply_layout(spec, layout_name=name)
        except Exception as e:
            return {"content": [{"type": "text", "text": f"Apply error: {e}"}]}
        return {"content": [{"type": "text", "text": f"Loaded layout {name!r}."}]}
    return load_layout_tool


def _list_layouts_handler(layouts_store: NamedLayoutsStore):
    async def list_layouts_tool(_args: dict) -> dict:
        names = layouts_store.list()
        text = json.dumps(names)
        return {"content": [{"type": "text", "text": text}]}
    return list_layouts_tool
```

Modify `build_orchestrator_tools` and `build_orchestrator_mcp_server` to accept the new args:

```python
def build_orchestrator_tools(
    manager: AgentManager,
    *,
    apply_layout=None,
    layouts_store: NamedLayoutsStore | None = None,
):
    """Return bare async handlers (for unit testing).

    apply_layout: async callable (spec, *, layout_name=None) -> None applying a
    LayoutSpec to the live UI. If None, set_layout / load_layout are omitted.
    layouts_store: NamedLayoutsStore for save/load/list. If None, the
    save/load/list tools are omitted.
    """
    handlers = [spec.build(manager) for spec in _SPECS]
    if apply_layout is not None and layouts_store is not None:
        handlers.append(_set_layout_handler(apply_layout))
        handlers.append(_save_layout_handler(layouts_store))
        handlers.append(_load_layout_handler(apply_layout, layouts_store))
        handlers.append(_list_layouts_handler(layouts_store))
    return tuple(handlers)


def build_orchestrator_mcp_server(
    manager: AgentManager,
    *,
    apply_layout=None,
    layouts_store: NamedLayoutsStore | None = None,
):
    sdk_tools = []
    for spec in _SPECS:
        handler = spec.build(manager)
        decorated = tool(spec.name, spec.description, spec.input_schema)(handler)
        sdk_tools.append(decorated)
    if apply_layout is not None and layouts_store is not None:
        layout_specs = [
            ("set_layout",
             "Replace the current UI layout with the given LayoutSpec dict.",
             {"spec": dict},
             _set_layout_handler(apply_layout)),
            ("save_layout",
             "Save the given LayoutSpec under a name in ~/.config/patchfeld/layouts/.",
             {"name": str, "spec": dict},
             _save_layout_handler(layouts_store)),
            ("load_layout",
             "Load and apply a previously-saved layout by name.",
             {"name": str},
             _load_layout_handler(apply_layout, layouts_store)),
            ("list_layouts",
             "List the names of all saved layouts.",
             {},
             _list_layouts_handler(layouts_store)),
        ]
        for name, desc, schema, handler in layout_specs:
            sdk_tools.append(tool(name, desc, schema)(handler))
    return create_sdk_mcp_server(
        name="patchfeld_orchestrator", version="1.0.0", tools=sdk_tools,
    )
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest tests/test_orchestrator_tools_layout.py tests/test_orchestrator_tools.py -v
.venv/bin/pytest -q
```

Expected: 4 new pass; existing tools tests still pass (they call `build_orchestrator_tools(manager)` without the new kwargs, which still returns the original 7-tuple).

- [ ] **Step 5: Commit**

```bash
git add patchfeld/orchestrator/tools.py tests/test_orchestrator_tools_layout.py
git commit -m "feat(orchestrator): set_layout / save_layout / load_layout / list_layouts MCP tools"
```

---

## Task 9 — `LayoutSwitcherScreen` modal (ctrl-l)

**Files:**
- Create: `patchfeld/widgets/layout_switcher.py`
- Test: `tests/test_layout_switcher.py`

A `ModalScreen` that lists the named layouts from `NamedLayoutsStore`. Selecting a row dismisses with the chosen name. The App's `ctrl-l` action pushes this screen and on dismiss calls `load_layout(name)` via the orchestrator's tool path.

- [ ] **Step 1: Write the failing test**

Create `tests/test_layout_switcher.py`:

```python
import pytest
from textual.app import App
from textual.widgets import ListView

from patchfeld.layout.defaults import dashboard_layout
from patchfeld.persistence.layouts_store import NamedLayoutsStore
from patchfeld.widgets.layout_switcher import LayoutSwitcherScreen


@pytest.mark.asyncio
async def test_switcher_lists_saved_names(tmp_path):
    store = NamedLayoutsStore(global_dir=tmp_path)
    store.save("alpha", dashboard_layout())
    store.save("beta", dashboard_layout())

    selected: list[str | None] = []

    class _Host(App):
        async def on_mount(self):
            screen = LayoutSwitcherScreen(store=store)

            def _capture(name):
                selected.append(name)

            await self.push_screen(screen, _capture)

    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        list_view = screen.query_one(ListView)
        # ListView populates with one item per layout name.
        items = list(list_view.children)
        assert len(items) == 2
        # Dismiss with first selection.
        screen.dismiss("alpha")
        await pilot.pause()

    assert selected == ["alpha"]


@pytest.mark.asyncio
async def test_switcher_dismisses_with_none_on_escape(tmp_path):
    store = NamedLayoutsStore(global_dir=tmp_path)
    store.save("only-one", dashboard_layout())

    selected: list[str | None] = []

    class _Host(App):
        async def on_mount(self):
            await self.push_screen(LayoutSwitcherScreen(store=store), selected.append)

    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()

    assert selected == [None]
```

- [ ] **Step 2: Run and confirm failure**

```bash
.venv/bin/pytest tests/test_layout_switcher.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement `patchfeld/widgets/layout_switcher.py`**

```python
from textual.binding import Binding
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import Footer, Label, ListItem, ListView

from patchfeld.persistence.layouts_store import NamedLayoutsStore


class LayoutSwitcherScreen(ModalScreen[str | None]):
    """Pick a saved layout. Esc dismisses with None; selecting dismisses with the name."""

    DEFAULT_CSS = """
    LayoutSwitcherScreen {
        align: center middle;
    }
    LayoutSwitcherScreen > Container {
        width: 50%;
        height: 60%;
        border: thick $primary;
        background: $surface;
        padding: 1 2;
    }
    LayoutSwitcherScreen ListView {
        height: 1fr;
    }
    """

    BINDINGS = [Binding("escape", "dismiss_none", "cancel")]

    def __init__(self, store: NamedLayoutsStore) -> None:
        super().__init__()
        self._store = store

    def compose(self):
        items = [ListItem(Label(name), name=name) for name in self._store.list()]
        with Container():
            yield Label("Load layout:")
            yield ListView(*items)
            yield Footer()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        # Each ListItem has the name baked in via the `name=` kwarg.
        self.dismiss(event.item.name)

    def action_dismiss_none(self) -> None:
        self.dismiss(None)
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest tests/test_layout_switcher.py -v
.venv/bin/pytest -q
```

Expected: 2 new pass.

If `event.item.name` doesn't work (Textual API may have changed; the `ListItem`'s `name` is sometimes accessed as `event.item.id` or via a custom attribute), adjust accordingly. STOP and report if the `ListView.Selected` event surface is unfamiliar.

- [ ] **Step 5: Commit**

```bash
git add patchfeld/widgets/layout_switcher.py tests/test_layout_switcher.py
git commit -m "feat(widgets): LayoutSwitcherScreen modal for picking a named layout"
```

---

## Task 10 — `ConfigStore` (TOML-backed)

**Files:**
- Create: `patchfeld/config.py`
- Test: `tests/test_config_store.py`
- Modify: `pyproject.toml` (add `tomli-w` dep)

`ConfigStore` reads/writes `~/.config/patchfeld/config.toml` with a typed schema:

```toml
[bindings]
"/" = { action = "focus_command_bar" }
"ctrl+q" = { action = "quit" }
"ctrl+h" = { action = "open_history" }
"ctrl+l" = { action = "open_layout_switcher" }
"?" = { action = "show_help" }

[ui]
theme = "dark"
default_model = ""  # empty = use SDK default
```

Read with stdlib `tomllib`; write with `tomli-w`.

- [ ] **Step 1: Add `tomli-w` to deps**

In `pyproject.toml`, extend the `[project] dependencies` block:

```toml
dependencies = [
  "textual>=0.80",
  "pydantic>=2.6",
  "claude-agent-sdk>=0.1",
  "tomli-w>=1.0",
]
```

Run:

```bash
cd /Users/jimmy.mills/Developer/patchfeld && uv pip install -e ".[dev]"
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_config_store.py`:

```python
from pathlib import Path

import pytest

from patchfeld.config import ConfigStore, KeyBinding


def test_load_returns_defaults_when_no_file(tmp_path: Path):
    store = ConfigStore(global_dir=tmp_path)
    cfg = store.load()
    assert cfg.bindings  # non-empty defaults
    assert cfg.ui.theme == "dark"
    assert cfg.ui.default_model == ""


def test_save_then_load_round_trip(tmp_path: Path):
    store = ConfigStore(global_dir=tmp_path)
    cfg = store.load()
    cfg.bindings["~"] = KeyBinding(action="focus_orchestrator", args={})
    cfg.ui.theme = "light"
    store.save(cfg)

    again = store.load()
    assert again.bindings["~"].action == "focus_orchestrator"
    assert again.ui.theme == "light"


def test_save_creates_config_dir(tmp_path: Path):
    store = ConfigStore(global_dir=tmp_path)
    store.save(store.load())
    assert (tmp_path / "config.toml").exists()


def test_set_path_dotted(tmp_path: Path):
    store = ConfigStore(global_dir=tmp_path)
    cfg = store.load()
    cfg.set_path("ui.theme", "light")
    assert cfg.ui.theme == "light"

    cfg.set_path("ui.default_model", "claude-sonnet-4-6")
    assert cfg.ui.default_model == "claude-sonnet-4-6"


def test_get_path_dotted(tmp_path: Path):
    store = ConfigStore(global_dir=tmp_path)
    cfg = store.load()
    assert cfg.get_path("ui.theme") == "dark"
    assert cfg.get_path("ui.default_model") == ""


def test_get_path_unknown_raises(tmp_path: Path):
    store = ConfigStore(global_dir=tmp_path)
    cfg = store.load()
    with pytest.raises(KeyError):
        cfg.get_path("nonexistent.field")
```

- [ ] **Step 3: Run and confirm they fail**

```bash
.venv/bin/pytest tests/test_config_store.py -v
```

Expected: ImportError.

- [ ] **Step 4: Implement `patchfeld/config.py`**

```python
import logging
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import tomli_w

log = logging.getLogger(__name__)


_DEFAULT_BINDINGS = {
    "/":      ("focus_command_bar", {}),
    "ctrl+q": ("quit", {}),
    "ctrl+h": ("open_history", {}),
    "ctrl+l": ("open_layout_switcher", {}),
    "?":      ("show_help", {}),
}


@dataclass
class KeyBinding:
    action: str
    args: dict = field(default_factory=dict)


@dataclass
class UISection:
    theme: str = "dark"
    default_model: str = ""


@dataclass
class Config:
    bindings: dict[str, KeyBinding] = field(default_factory=dict)
    ui: UISection = field(default_factory=UISection)

    def get_path(self, path: str) -> Any:
        parts = path.split(".")
        if len(parts) != 2:
            raise KeyError(f"only dotted two-segment paths supported, got {path!r}")
        section, attr = parts
        if section == "ui" and hasattr(self.ui, attr):
            return getattr(self.ui, attr)
        raise KeyError(path)

    def set_path(self, path: str, value: Any) -> None:
        parts = path.split(".")
        if len(parts) != 2:
            raise KeyError(f"only dotted two-segment paths supported, got {path!r}")
        section, attr = parts
        if section == "ui" and hasattr(self.ui, attr):
            setattr(self.ui, attr, value)
            return
        raise KeyError(path)


class ConfigStore:
    """Read/write ~/.config/patchfeld/config.toml. Defaults applied on missing file."""

    def __init__(self, global_dir: Path) -> None:
        self._dir = Path(global_dir)
        self._path = self._dir / "config.toml"

    def load(self) -> Config:
        cfg = Config()
        # Apply defaults first.
        for key, (action, args) in _DEFAULT_BINDINGS.items():
            cfg.bindings[key] = KeyBinding(action=action, args=dict(args))

        if not self._path.exists():
            return cfg

        try:
            raw = tomllib.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            log.exception("Failed to parse config.toml; using defaults")
            return cfg

        # Merge bindings (overrides defaults).
        bindings_raw = raw.get("bindings", {})
        if isinstance(bindings_raw, dict):
            for key, val in bindings_raw.items():
                if isinstance(val, dict) and "action" in val:
                    cfg.bindings[key] = KeyBinding(
                        action=val["action"], args=dict(val.get("args", {}))
                    )

        ui_raw = raw.get("ui", {})
        if isinstance(ui_raw, dict):
            if "theme" in ui_raw and isinstance(ui_raw["theme"], str):
                cfg.ui.theme = ui_raw["theme"]
            if "default_model" in ui_raw and isinstance(ui_raw["default_model"], str):
                cfg.ui.default_model = ui_raw["default_model"]
        return cfg

    def save(self, cfg: Config) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        out = {
            "bindings": {
                key: {"action": b.action, "args": b.args}
                for key, b in cfg.bindings.items()
            },
            "ui": {"theme": cfg.ui.theme, "default_model": cfg.ui.default_model},
        }
        self._path.write_text(tomli_w.dumps(out), encoding="utf-8")
```

- [ ] **Step 5: Run tests**

```bash
.venv/bin/pytest tests/test_config_store.py -v
.venv/bin/pytest -q
```

Expected: 6 new pass; full suite green.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml patchfeld/config.py tests/test_config_store.py
git commit -m "feat(config): ConfigStore with TOML round-trip and dotted get/set"
```

---

## Task 11 — `ActionRegistry`

**Files:**
- Create: `patchfeld/actions.py`
- Test: `tests/test_action_registry.py`

`ActionRegistry` is a dict from action name → (callable, signature description). The App registers actions during boot. `bind_key` looks up the action by name; `list_actions` enumerates them for the orchestrator AI.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_action_registry.py`:

```python
import pytest

from patchfeld.actions import ActionRegistry, ActionSpec


def test_register_then_lookup():
    reg = ActionRegistry()

    def my_action():
        return "ran"

    reg.register("my_action", my_action, description="does the thing", args_schema={})
    spec = reg.get("my_action")
    assert spec.callable is my_action
    assert spec.description == "does the thing"
    assert spec.args_schema == {}


def test_list_returns_specs_sorted_by_name():
    reg = ActionRegistry()
    reg.register("zeta", lambda: None, description="z", args_schema={})
    reg.register("alpha", lambda: None, description="a", args_schema={})
    names = [s.name for s in reg.list()]
    assert names == ["alpha", "zeta"]


def test_get_unknown_raises_keyerror():
    reg = ActionRegistry()
    with pytest.raises(KeyError):
        reg.get("nope")


def test_register_overrides_existing():
    reg = ActionRegistry()
    reg.register("act", lambda: 1, description="v1", args_schema={})
    reg.register("act", lambda: 2, description="v2", args_schema={})
    assert reg.get("act").description == "v2"


def test_invoke_calls_with_args():
    reg = ActionRegistry()
    captured: list = []
    def my_act(panel_id: str):
        captured.append(panel_id)
    reg.register("my_act", my_act, description="x", args_schema={"panel_id": str})

    reg.invoke("my_act", {"panel_id": "orch"})
    assert captured == ["orch"]
```

- [ ] **Step 2: Run and confirm failure**

```bash
.venv/bin/pytest tests/test_action_registry.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement `patchfeld/actions.py`**

```python
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class ActionSpec:
    name: str
    callable: Callable
    description: str
    args_schema: dict


class ActionRegistry:
    """Enumerable action registry — name → ActionSpec."""

    def __init__(self) -> None:
        self._actions: dict[str, ActionSpec] = {}

    def register(self, name: str, fn: Callable, *, description: str, args_schema: dict) -> None:
        self._actions[name] = ActionSpec(
            name=name, callable=fn, description=description, args_schema=args_schema,
        )

    def get(self, name: str) -> ActionSpec:
        if name not in self._actions:
            raise KeyError(f"unknown action: {name}")
        return self._actions[name]

    def list(self) -> list[ActionSpec]:
        return sorted(self._actions.values(), key=lambda s: s.name)

    def invoke(self, name: str, args: dict) -> Any:
        spec = self.get(name)
        return spec.callable(**(args or {}))
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest tests/test_action_registry.py -v
.venv/bin/pytest -q
```

Expected: 5 new pass; full suite green.

- [ ] **Step 5: Commit**

```bash
git add patchfeld/actions.py tests/test_action_registry.py
git commit -m "feat(actions): ActionRegistry with register/get/list/invoke"
```

---

## Task 12 — Orchestrator MCP tools: `bind_key`, `unbind_key`, `set_config`, `get_config`, `list_actions`, `list_bindings`

**Files:**
- Modify: `patchfeld/orchestrator/tools.py`
- Test: `tests/test_orchestrator_tools_config.py`

Six new tools. They take `ConfigStore`, `ActionRegistry`, and an optional "rebind" callable that triggers the App to re-apply its bindings live.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_orchestrator_tools_config.py`:

```python
import json
from pathlib import Path

import pytest

from patchfeld.actions import ActionRegistry
from patchfeld.agents.fake_sdk_adapter import FakeSDKAdapter
from patchfeld.agents.manager import AgentManager
from patchfeld.config import ConfigStore
from patchfeld.events import EventBus
from patchfeld.orchestrator.tools import build_orchestrator_tools


def _make(tmp_path, ok_script):
    manager = AgentManager(
        cwd=tmp_path,
        bus=EventBus(),
        adapter_factory=lambda: FakeSDKAdapter(scripts=[ok_script()]),
    )
    config_store = ConfigStore(global_dir=tmp_path)
    actions = ActionRegistry()
    actions.register(
        "focus_orchestrator", lambda: None,
        description="Focus the orchestrator chat panel.", args_schema={},
    )
    actions.register(
        "focus_panel", lambda panel_id: None,
        description="Focus a specific panel by id.", args_schema={"panel_id": str},
    )
    rebound: list[bool] = []
    def rebind():
        rebound.append(True)
    return manager, config_store, actions, rebind, rebound


@pytest.mark.asyncio
async def test_bind_key_persists_and_triggers_rebind(tmp_path, ok_script):
    manager, store, actions, rebind, rebound = _make(tmp_path, ok_script)
    tools = build_orchestrator_tools(
        manager,
        config_store=store,
        actions=actions,
        rebind_keys=rebind,
    )
    # Tuple is 7 base + 6 config = 13 entries; bind_key is the 8th (index 7).
    bind_key = tools[7]

    out = await bind_key({"key": "~", "action": "focus_orchestrator"})
    text = out["content"][0]["text"].lower()
    assert "bound" in text
    assert rebound == [True]

    # Persisted.
    cfg = store.load()
    assert cfg.bindings["~"].action == "focus_orchestrator"


@pytest.mark.asyncio
async def test_bind_key_unknown_action_returns_error(tmp_path, ok_script):
    manager, store, actions, rebind, _ = _make(tmp_path, ok_script)
    tools = build_orchestrator_tools(manager, config_store=store, actions=actions, rebind_keys=rebind)
    bind_key = tools[7]
    out = await bind_key({"key": "~", "action": "no_such_action"})
    assert "unknown action" in out["content"][0]["text"].lower()


@pytest.mark.asyncio
async def test_unbind_key_removes_binding(tmp_path, ok_script):
    manager, store, actions, rebind, _ = _make(tmp_path, ok_script)
    tools = build_orchestrator_tools(manager, config_store=store, actions=actions, rebind_keys=rebind)
    bind_key = tools[7]
    unbind_key = tools[8]

    await bind_key({"key": "~", "action": "focus_orchestrator"})
    out = await unbind_key({"key": "~"})
    assert "unbound" in out["content"][0]["text"].lower()
    cfg = store.load()
    assert "~" not in cfg.bindings


@pytest.mark.asyncio
async def test_set_config_dotted_path(tmp_path, ok_script):
    manager, store, actions, rebind, _ = _make(tmp_path, ok_script)
    tools = build_orchestrator_tools(manager, config_store=store, actions=actions, rebind_keys=rebind)
    set_config = tools[9]
    get_config = tools[10]

    await set_config({"path": "ui.theme", "value": "light"})
    out = await get_config({"path": "ui.theme"})
    assert "light" in out["content"][0]["text"]


@pytest.mark.asyncio
async def test_list_actions_returns_json(tmp_path, ok_script):
    manager, store, actions, rebind, _ = _make(tmp_path, ok_script)
    tools = build_orchestrator_tools(manager, config_store=store, actions=actions, rebind_keys=rebind)
    list_actions = tools[11]

    out = await list_actions({})
    parsed = json.loads(out["content"][0]["text"])
    names = {a["name"] for a in parsed}
    assert "focus_orchestrator" in names
    assert "focus_panel" in names


@pytest.mark.asyncio
async def test_list_bindings_returns_current_bindings(tmp_path, ok_script):
    manager, store, actions, rebind, _ = _make(tmp_path, ok_script)
    tools = build_orchestrator_tools(manager, config_store=store, actions=actions, rebind_keys=rebind)
    bind_key = tools[7]
    list_bindings = tools[12]

    await bind_key({"key": "~", "action": "focus_orchestrator"})
    out = await list_bindings({})
    parsed = json.loads(out["content"][0]["text"])
    assert any(b["key"] == "~" for b in parsed)
```

- [ ] **Step 2: Run and confirm failures**

```bash
.venv/bin/pytest tests/test_orchestrator_tools_config.py -v
```

Expected: failures (the new tools don't exist yet; `build_orchestrator_tools` doesn't accept the new kwargs).

- [ ] **Step 3: Modify `patchfeld/orchestrator/tools.py`**

Add these handler builders ABOVE `_SPECS`:

```python
from patchfeld.actions import ActionRegistry
from patchfeld.config import ConfigStore, KeyBinding


def _bind_key_handler(config_store: ConfigStore, actions: ActionRegistry, rebind):
    async def bind_key_tool(args: dict) -> dict:
        key = args["key"]
        action = args["action"]
        bind_args = args.get("args", {})
        try:
            actions.get(action)
        except KeyError:
            return {"content": [{"type": "text", "text": f"Unknown action: {action}"}]}
        cfg = config_store.load()
        cfg.bindings[key] = KeyBinding(action=action, args=dict(bind_args))
        config_store.save(cfg)
        if rebind is not None:
            rebind()
        return {"content": [{"type": "text", "text": f"Bound {key!r} → {action}."}]}
    return bind_key_tool


def _unbind_key_handler(config_store: ConfigStore, rebind):
    async def unbind_key_tool(args: dict) -> dict:
        key = args["key"]
        cfg = config_store.load()
        if key in cfg.bindings:
            del cfg.bindings[key]
            config_store.save(cfg)
            if rebind is not None:
                rebind()
            return {"content": [{"type": "text", "text": f"Unbound {key!r}."}]}
        return {"content": [{"type": "text", "text": f"No binding for {key!r}."}]}
    return unbind_key_tool


def _set_config_handler(config_store: ConfigStore):
    async def set_config_tool(args: dict) -> dict:
        path = args["path"]
        value = args["value"]
        cfg = config_store.load()
        try:
            cfg.set_path(path, value)
        except KeyError:
            return {"content": [{"type": "text", "text": f"Unknown config path: {path}"}]}
        config_store.save(cfg)
        return {"content": [{"type": "text", "text": f"Set {path} = {value!r}."}]}
    return set_config_tool


def _get_config_handler(config_store: ConfigStore):
    async def get_config_tool(args: dict) -> dict:
        path = args["path"]
        cfg = config_store.load()
        try:
            value = cfg.get_path(path)
        except KeyError:
            return {"content": [{"type": "text", "text": f"Unknown config path: {path}"}]}
        return {"content": [{"type": "text", "text": json.dumps(value)}]}
    return get_config_tool


def _list_actions_handler(actions: ActionRegistry):
    async def list_actions_tool(_args: dict) -> dict:
        out = [
            {"name": s.name, "description": s.description, "args_schema": list(s.args_schema.keys())}
            for s in actions.list()
        ]
        return {"content": [{"type": "text", "text": json.dumps(out, indent=2)}]}
    return list_actions_tool


def _list_bindings_handler(config_store: ConfigStore):
    async def list_bindings_tool(_args: dict) -> dict:
        cfg = config_store.load()
        out = [
            {"key": k, "action": b.action, "args": b.args}
            for k, b in sorted(cfg.bindings.items())
        ]
        return {"content": [{"type": "text", "text": json.dumps(out, indent=2)}]}
    return list_bindings_tool
```

Extend `build_orchestrator_tools` and `build_orchestrator_mcp_server` to accept the new kwargs:

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
    handlers = [spec.build(manager) for spec in _SPECS]
    if apply_layout is not None and layouts_store is not None:
        handlers.append(_set_layout_handler(apply_layout))
        handlers.append(_save_layout_handler(layouts_store))
        handlers.append(_load_layout_handler(apply_layout, layouts_store))
        handlers.append(_list_layouts_handler(layouts_store))
    if config_store is not None and actions is not None:
        handlers.append(_bind_key_handler(config_store, actions, rebind_keys))
        handlers.append(_unbind_key_handler(config_store, rebind_keys))
        handlers.append(_set_config_handler(config_store))
        handlers.append(_get_config_handler(config_store))
        handlers.append(_list_actions_handler(actions))
        handlers.append(_list_bindings_handler(config_store))
    return tuple(handlers)


def build_orchestrator_mcp_server(
    manager: AgentManager,
    *,
    apply_layout=None,
    layouts_store: NamedLayoutsStore | None = None,
    config_store: ConfigStore | None = None,
    actions: ActionRegistry | None = None,
    rebind_keys=None,
):
    sdk_tools = []
    for spec in _SPECS:
        handler = spec.build(manager)
        decorated = tool(spec.name, spec.description, spec.input_schema)(handler)
        sdk_tools.append(decorated)
    if apply_layout is not None and layouts_store is not None:
        layout_specs = [
            ("set_layout",
             "Replace the current UI layout with the given LayoutSpec dict.",
             {"spec": dict},
             _set_layout_handler(apply_layout)),
            ("save_layout",
             "Save the given LayoutSpec under a name in ~/.config/patchfeld/layouts/.",
             {"name": str, "spec": dict},
             _save_layout_handler(layouts_store)),
            ("load_layout",
             "Load and apply a previously-saved layout by name.",
             {"name": str},
             _load_layout_handler(apply_layout, layouts_store)),
            ("list_layouts",
             "List the names of all saved layouts.",
             {},
             _list_layouts_handler(layouts_store)),
        ]
        for name, desc, schema, handler in layout_specs:
            sdk_tools.append(tool(name, desc, schema)(handler))
    if config_store is not None and actions is not None:
        config_specs = [
            ("bind_key",
             "Bind a key (e.g., 'ctrl+x', '~') to a registered action. "
             "Optional `args` dict is passed to the action when invoked.",
             {"key": str, "action": str},
             _bind_key_handler(config_store, actions, rebind_keys)),
            ("unbind_key",
             "Remove the binding for the given key.",
             {"key": str},
             _unbind_key_handler(config_store, rebind_keys)),
            ("set_config",
             "Set a config value by dotted path (e.g., 'ui.theme').",
             {"path": str, "value": str},
             _set_config_handler(config_store)),
            ("get_config",
             "Read a config value by dotted path. Returns the value as JSON.",
             {"path": str},
             _get_config_handler(config_store)),
            ("list_actions",
             "List all registered keybinding actions.",
             {},
             _list_actions_handler(actions)),
            ("list_bindings",
             "List all current keybindings.",
             {},
             _list_bindings_handler(config_store)),
        ]
        for name, desc, schema, handler in config_specs:
            sdk_tools.append(tool(name, desc, schema)(handler))
    return create_sdk_mcp_server(
        name="patchfeld_orchestrator", version="1.0.0", tools=sdk_tools,
    )
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest tests/test_orchestrator_tools_config.py tests/test_orchestrator_tools.py tests/test_orchestrator_tools_layout.py -v
.venv/bin/pytest -q
```

Expected: 6 new pass; existing tools tests still pass (they don't pass the new kwargs).

- [ ] **Step 5: Commit**

```bash
git add patchfeld/orchestrator/tools.py tests/test_orchestrator_tools_config.py
git commit -m "feat(orchestrator): bind_key / unbind_key / set_config / get_config / list_actions / list_bindings tools"
```

---

## Task 13 — `HistoryScreen` modal (ctrl-h)

**Files:**
- Create: `patchfeld/widgets/history_screen.py`
- Test: `tests/test_history_screen.py`

A `ModalScreen` listing every agent ever recorded in `agents.json`. Selecting a row dismisses with the agent_id; the App opens a `TranscriptScreen` for that id. (Clicking a finished agent's transcript is the headline UX of the History view.)

- [ ] **Step 1: Write the failing test**

Create `tests/test_history_screen.py`:

```python
import pytest
from textual.app import App
from textual.widgets import DataTable

from patchfeld.agents.state import AgentInfo, AgentState
from patchfeld.persistence.agents_index import AgentsIndex
from patchfeld.widgets.history_screen import HistoryScreen


@pytest.mark.asyncio
async def test_history_lists_agents_from_index(tmp_path):
    idx = AgentsIndex(cwd=tmp_path)
    idx.upsert(AgentInfo(id="a1", name="alpha", cwd="/tmp", started_at=100.0,
                         state=AgentState.DONE, ended_at=200.0))
    idx.upsert(AgentInfo(id="a2", name="beta", cwd="/tmp", started_at=300.0,
                         state=AgentState.DONE, ended_at=400.0))

    selected: list[str | None] = []

    class _Host(App):
        async def on_mount(self):
            screen = HistoryScreen(index=idx)
            await self.push_screen(screen, selected.append)

    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        table = screen.query_one(DataTable)
        assert table.row_count == 2
        # Programmatically dismiss with the first agent id.
        screen.dismiss("a1")
        await pilot.pause()

    assert selected == ["a1"]


@pytest.mark.asyncio
async def test_history_dismisses_with_none_on_escape(tmp_path):
    idx = AgentsIndex(cwd=tmp_path)
    idx.upsert(AgentInfo(id="a1", name="alpha", cwd="/tmp", started_at=100.0))

    selected: list[str | None] = []

    class _Host(App):
        async def on_mount(self):
            await self.push_screen(HistoryScreen(index=idx), selected.append)

    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()

    assert selected == [None]
```

- [ ] **Step 2: Run and confirm failure**

```bash
.venv/bin/pytest tests/test_history_screen.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement `patchfeld/widgets/history_screen.py`**

```python
from textual.binding import Binding
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Label

from patchfeld.persistence.agents_index import AgentsIndex


class HistoryScreen(ModalScreen[str | None]):
    """Modal listing every agent in agents.json. Selecting dismisses with the id."""

    DEFAULT_CSS = """
    HistoryScreen {
        align: center middle;
    }
    HistoryScreen > Container {
        width: 75%;
        height: 75%;
        border: thick $primary;
        background: $surface;
        padding: 1 2;
    }
    HistoryScreen DataTable {
        height: 1fr;
    }
    """

    BINDINGS = [Binding("escape", "dismiss_none", "cancel")]

    COLUMNS = ("id", "name", "state", "started", "cost")

    def __init__(self, index: AgentsIndex) -> None:
        super().__init__()
        self._index = index

    def compose(self):
        with Container():
            yield Label("Agent history (Enter to view transcript, Esc to close):")
            table = DataTable(zebra_stripes=True, cursor_type="row")
            for col in self.COLUMNS:
                table.add_column(col, key=col)
            for info in self._index.load():
                table.add_row(
                    info.id,
                    info.name,
                    info.state.value,
                    f"{info.started_at:.0f}",
                    f"${info.cost:.4f}",
                    key=info.id,
                )
            yield table
            yield Footer()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.dismiss(str(event.row_key.value))

    def action_dismiss_none(self) -> None:
        self.dismiss(None)
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest tests/test_history_screen.py -v
.venv/bin/pytest -q
```

Expected: 2 new pass; full suite green.

- [ ] **Step 5: Commit**

```bash
git add patchfeld/widgets/history_screen.py tests/test_history_screen.py
git commit -m "feat(widgets): HistoryScreen modal listing agents.json entries"
```

---

## Task 14 — Wire layout/config tools + history/switcher into the App

**Files:**
- Modify: `patchfeld/app.py`
- Modify: `patchfeld/widgets/chrome.py` (subscribe StatusBar to LayoutApplied)

The App now:
1. Builds a `ConfigStore`, `NamedLayoutsStore`, and `ActionRegistry` on startup
2. Loads bindings from config and registers them dynamically (replacing the hard-coded `BINDINGS` class var with runtime bindings)
3. Registers actions: `focus_orchestrator`, `focus_panel(panel_id)`, `focus_command_bar`, `cycle_focus`, `quit`, `open_history`, `open_layout_switcher`, `show_help`
4. Wires the orchestrator's tools with `apply_layout`, `layouts_store`, `config_store`, `actions`, `rebind_keys`
5. Has `action_open_history` (push HistoryScreen → on dismiss, push TranscriptScreen for the selected agent)
6. Has `action_open_layout_switcher` (push LayoutSwitcherScreen → on dismiss, call load_layout via the orchestrator's tool path)
7. Provides `_apply_layout(spec, layout_name=None)` which the orchestrator's `set_layout`/`load_layout` tools call
8. Provides `_rebind_keys()` which loads bindings from config and re-registers them

Also: StatusBar subscribes to `LayoutApplied` and updates its layout-name field.

- [ ] **Step 1: Modify `patchfeld/widgets/chrome.py`** — add a LayoutApplied subscription to StatusBar

In `StatusBar.on_mount`, alongside the existing `StatsUpdated` subscription, add:

```python
        from patchfeld.events import LayoutApplied
        self._unsub_layout = bus.subscribe(LayoutApplied, self._on_layout_applied)
```

Also extend `__init__` to track `self._unsub_layout = lambda: None`. And in `on_unmount`, call `self._unsub_layout()`.

Add the new handler method:

```python
    def _on_layout_applied(self, event) -> None:
        name = event.layout_name or "default"
        self.set_layout_name(name)
```

- [ ] **Step 2: Replace `patchfeld/app.py`**

```python
from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.widgets import DataTable

from patchfeld.actions import ActionRegistry
from patchfeld.agents.manager import AgentManager
from patchfeld.agents.sdk_adapter import RealSDKAdapter
from patchfeld.config import ConfigStore
from patchfeld.events import EventBus
from patchfeld.layout.defaults import dashboard_layout
from patchfeld.layout.engine import apply as apply_layout
from patchfeld.layout.registry import WidgetRegistry
from patchfeld.layout.spec import LayoutSpec
from patchfeld.orchestrator.session import OrchestratorSession
from patchfeld.persistence.layout_store import load_layout as load_local_layout
from patchfeld.persistence.layout_store import save_layout as save_local_layout
from patchfeld.persistence.layouts_store import NamedLayoutsStore
from patchfeld.persistence.paths import global_config_dir
from patchfeld.persistence.agents_index import AgentsIndex
from patchfeld.persistence.transcript_store import OrchestratorTranscript
from patchfeld.widgets.agent_table import AgentTable
from patchfeld.widgets.chrome import CommandBar, StatusBar
from patchfeld.widgets.history_screen import HistoryScreen
from patchfeld.widgets.layout_switcher import LayoutSwitcherScreen
from patchfeld.widgets.orchestrator_chat import OrchestratorChat
from patchfeld.widgets.placeholders import ActivityFeed
from patchfeld.widgets.transcript_screen import TranscriptScreen


def build_default_registry() -> WidgetRegistry:
    reg = WidgetRegistry()
    reg.register("OrchestratorChat", OrchestratorChat)
    reg.register("AgentTable", AgentTable)
    reg.register("ActivityFeed", ActivityFeed)
    return reg


class PatchfeldApp(App):
    """Plan-4 App: layout + config mutability via orchestrator MCP tools."""

    CSS = """
    #panel-area {
        height: 1fr;
    }
    """

    def __init__(
        self,
        *,
        cwd: Path | None = None,
        registry: WidgetRegistry | None = None,
        manager: AgentManager | None = None,
        orchestrator: OrchestratorSession | None = None,
        global_dir: Path | None = None,
    ) -> None:
        super().__init__()
        self.cwd = Path(cwd) if cwd else Path.cwd()
        self.event_bus = EventBus()
        self.registry = registry or build_default_registry()
        self._current_spec: LayoutSpec | None = None
        self._current_layout_name: str | None = None
        self.transcript = OrchestratorTranscript(cwd=self.cwd)
        self.orchestrator_history: list[tuple[str, str]] = [
            (e.role, e.text) for e in self.transcript.read_all()
        ]
        self._global_dir = Path(global_dir) if global_dir else global_config_dir()
        self.config_store = ConfigStore(global_dir=self._global_dir)
        self.layouts_store = NamedLayoutsStore(global_dir=self._global_dir)
        self.actions_registry = ActionRegistry()
        self._register_actions()
        self.manager = manager or AgentManager(
            cwd=self.cwd,
            bus=self.event_bus,
            adapter_factory=RealSDKAdapter,
        )
        self.orchestrator = orchestrator or OrchestratorSession(
            cwd=self.cwd,
            bus=self.event_bus,
            manager=self.manager,
            apply_layout=self._orchestrator_apply_layout,
            layouts_store=self.layouts_store,
            config_store=self.config_store,
            actions=self.actions_registry,
            rebind_keys=self._rebind_keys,
        )

    # --- bindings ---------------------------------------------------------

    def _register_actions(self) -> None:
        self.actions_registry.register(
            "focus_command_bar", self.action_focus_command_bar,
            description="Move focus to the top command bar.", args_schema={},
        )
        self.actions_registry.register(
            "focus_orchestrator",
            lambda: self._focus_panel("orch"),
            description="Focus the orchestrator chat panel.", args_schema={},
        )
        self.actions_registry.register(
            "focus_panel",
            lambda panel_id: self._focus_panel(panel_id),
            description="Focus a specific panel by id.", args_schema={"panel_id": str},
        )
        self.actions_registry.register(
            "cycle_focus", self.action_focus_next,
            description="Move focus to the next focusable widget.", args_schema={},
        )
        self.actions_registry.register(
            "quit", self.action_quit,
            description="Quit the application.", args_schema={},
        )
        self.actions_registry.register(
            "show_help", self.action_show_help,
            description="Show the keybindings help notification.", args_schema={},
        )
        self.actions_registry.register(
            "open_history", self.action_open_history,
            description="Open the agent history modal.", args_schema={},
        )
        self.actions_registry.register(
            "open_layout_switcher", self.action_open_layout_switcher,
            description="Open the saved-layouts switcher modal.", args_schema={},
        )

    def _focus_panel(self, panel_id: str) -> None:
        try:
            self.query_one(f"#panel-{panel_id}").focus()
        except Exception:
            pass

    def _rebind_keys(self) -> None:
        cfg = self.config_store.load()
        new_bindings = []
        for key, b in cfg.bindings.items():
            # Use the binding's `action` string as the Textual action name. We
            # invoke via a private dispatcher action that looks the action up in
            # the registry at fire time so re-binding doesn't require widget
            # remount.
            new_bindings.append(
                Binding(key, f"dispatch('{b.action}')", b.action, priority=True)
            )
        self._bindings = type(self).BINDINGS_DEFAULT[:]  # base copy
        for b in new_bindings:
            self._bindings.append(b)
        # Textual exposes refresh_bindings to reapply them live.
        try:
            self.refresh_bindings()
        except AttributeError:
            pass

    # Textual actions -----------------------------------------------------

    BINDINGS_DEFAULT: list[Binding] = []  # populated by _rebind_keys

    def action_dispatch(self, name: str) -> None:
        try:
            self.actions_registry.invoke(name, {})
        except KeyError:
            pass

    def action_focus_command_bar(self) -> None:
        self.query_one(CommandBar).focus_input()

    def action_show_help(self) -> None:
        self.notify(
            "/ command bar · ctrl-q quit · ctrl-h history · ctrl-l layouts · ? help",
            title="keybindings",
        )

    async def action_open_history(self) -> None:
        idx = AgentsIndex(cwd=self.cwd)
        agent_id = await self.push_screen_wait(HistoryScreen(index=idx))
        if agent_id:
            await self.push_screen(TranscriptScreen(agent_id=agent_id, event_bus=self.event_bus))

    async def action_open_layout_switcher(self) -> None:
        name = await self.push_screen_wait(LayoutSwitcherScreen(store=self.layouts_store))
        if name:
            spec = self.layouts_store.load(name)
            if spec is not None:
                await self._orchestrator_apply_layout(spec, layout_name=name)

    # Lifecycle -----------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield CommandBar(event_bus=self.event_bus)
        yield Container(id="panel-area")
        yield StatusBar(event_bus=self.event_bus)

    async def on_mount(self) -> None:
        self._rebind_keys()
        await self.orchestrator.start()
        spec = load_local_layout(self.cwd) or dashboard_layout()
        await self._apply(spec)

    async def _orchestrator_apply_layout(self, spec: LayoutSpec, *, layout_name: str | None = None) -> None:
        """Callable handed to the orchestrator's set_layout / load_layout tools."""
        await self._apply(spec, layout_name=layout_name)

    async def _apply(self, spec: LayoutSpec, *, layout_name: str | None = None) -> None:
        area = self.query_one("#panel-area", Container)
        await apply_layout(area, spec, self.registry, layout_name=layout_name)
        self._current_spec = spec
        self._current_layout_name = layout_name
        save_local_layout(self.cwd, spec)

    async def on_unmount(self) -> None:
        await self.orchestrator.stop()
        await self.manager.shutdown()

    async def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        # AgentTable rows use agent_id as their row key (HistoryScreen handles
        # its own row-selected via the modal).
        if isinstance(self.screen, (HistoryScreen, LayoutSwitcherScreen)):
            return
        agent_id = str(event.row_key.value)
        await self.push_screen(TranscriptScreen(agent_id=agent_id, event_bus=self.event_bus))
```

Note: `OrchestratorSession` needs new kwargs (`apply_layout`, `layouts_store`, `config_store`, `actions`, `rebind_keys`) that are forwarded to `build_orchestrator_mcp_server`. Update `OrchestratorSession.__init__` to accept and store them, and `start` to forward them. (Brief: add the kwargs as `self._...`, pass them to `build_orchestrator_mcp_server(self._manager, apply_layout=..., layouts_store=..., config_store=..., actions=..., rebind_keys=...)`.)

- [ ] **Step 3: Modify `OrchestratorSession.__init__` and `start` to accept and forward the new kwargs**

In `patchfeld/orchestrator/session.py`, replace `__init__` to add the kwargs:

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
    ) -> None:
        self._cwd = cwd
        self._bus = bus
        self._manager = manager
        self._model = model
        self._adapter = adapter or RealSDKAdapter()
        self._apply_layout = apply_layout
        self._layouts_store = layouts_store
        self._config_store = config_store
        self._actions = actions
        self._rebind_keys = rebind_keys
        # ...rest of __init__ identical to plan 3
```

In `start`, change the `build_orchestrator_mcp_server(self._manager)` call to:

```python
        mcp_server = build_orchestrator_mcp_server(
            self._manager,
            apply_layout=self._apply_layout,
            layouts_store=self._layouts_store,
            config_store=self._config_store,
            actions=self._actions,
            rebind_keys=self._rebind_keys,
        )
```

- [ ] **Step 4: Update `tests/test_app_smoke.py` to inject the layout/config kwargs (optional)**

The existing smoke tests construct `OrchestratorSession(cwd=..., bus=..., manager=..., adapter=...)`. They should still work because all the new kwargs default to None — the orchestrator just won't have layout/config tools. Verify no test broke.

- [ ] **Step 5: Run all tests**

```bash
.venv/bin/pytest -q
```

Expected: full suite green. The `_rebind_keys` action — `dispatch('action_name')` with `priority=True` — should not interfere with existing key handling (the action_dispatch indirection is a thin wrapper).

If anything broke (most likely Textual's `_bindings` attribute access is wrong, or `refresh_bindings` doesn't exist on this Textual version), STOP and report. The fallback for older Textual versions is to set `self.BINDINGS = new_bindings_list` and then call `self.refresh()` — describe what you did.

- [ ] **Step 6: Commit**

```bash
git add patchfeld/app.py patchfeld/orchestrator/session.py patchfeld/widgets/chrome.py
git commit -m "feat(app): wire layout/config tools + history/switcher modals + dynamic bindings"
```

---

## Task 15 — End-to-end test: orchestrator binds a key

**Files:**
- Create: `tests/test_app_smoke_plan4.py`

The headline plan-4 test: invoke the `bind_key` tool through `build_orchestrator_tools` with the same wiring the App uses, and verify that pressing the bound key triggers the registered action.

- [ ] **Step 1: Write the test**

Create `tests/test_app_smoke_plan4.py`:

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


def _build_app(tmp_path):
    bus = EventBus()
    manager = AgentManager(
        cwd=tmp_path, bus=bus,
        adapter_factory=lambda: FakeSDKAdapter(scripts=[_ok()]),
    )
    # Build a minimal app with a fake orchestrator that doesn't actually run,
    # so we can test the App's wiring of tools + bindings.
    app = PatchfeldApp(cwd=tmp_path, manager=manager, global_dir=tmp_path)
    app.event_bus = bus

    # Replace the orchestrator with one that uses fake adapters AND the
    # layout/config wiring the App set up.
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
async def test_bind_key_via_tool_then_press_triggers_action(tmp_path):
    app = _build_app(tmp_path)
    invocations: list[str] = []

    # Replace one registered action with a spy.
    app.actions_registry.register(
        "focus_orchestrator",
        lambda: invocations.append("focused"),
        description="spy", args_schema={},
    )

    async with app.run_test() as pilot:
        await pilot.pause()

        # Use the orchestrator's bind_key tool to bind ~ to focus_orchestrator.
        tools = build_orchestrator_tools(
            app.manager,
            apply_layout=app._orchestrator_apply_layout,
            layouts_store=app.layouts_store,
            config_store=app.config_store,
            actions=app.actions_registry,
            rebind_keys=app._rebind_keys,
        )
        bind_key = tools[7]
        await bind_key({"key": "~", "action": "focus_orchestrator"})
        await pilot.pause()

        # Press the newly-bound key.
        await pilot.press("~")
        await pilot.pause()

    assert invocations == ["focused"]
```

- [ ] **Step 2: Run**

```bash
.venv/bin/pytest tests/test_app_smoke_plan4.py -v
.venv/bin/pytest -q
```

Expected: 1 new pass; full suite green.

If pressing `~` doesn't trigger the action, the issue is likely in `_rebind_keys` — Textual's binding refresh API differs across versions. Investigate `App.refresh_bindings`, `App._bindings`, and `App.BINDINGS`. STOP and report what worked.

- [ ] **Step 3: Commit**

```bash
git add tests/test_app_smoke_plan4.py
git commit -m "test(app): plan-4 e2e — orchestrator binds a key, key press triggers action"
```

---

## Task 16 — Manual launch verification + tag `plan-4-complete`

- [ ] **Step 1: Imports**

```bash
cd /Users/jimmy.mills/Developer/patchfeld && .venv/bin/python -c "
from patchfeld.app import PatchfeldApp
from patchfeld.config import ConfigStore
from patchfeld.actions import ActionRegistry
from patchfeld.persistence.layouts_store import NamedLayoutsStore
from patchfeld.widgets.history_screen import HistoryScreen
from patchfeld.widgets.layout_switcher import LayoutSwitcherScreen
print('plan 4 imports OK')
"
```

Expected: `plan 4 imports OK`.

- [ ] **Step 2: Full suite green**

```bash
.venv/bin/pytest -v
```

Expected: every non-skipped test passes; the real-SDK smoke is skipped.

- [ ] **Step 3: Commit any leftover docs**

```bash
git status
```

Plan doc was committed earlier. Note any untracked files; don't add them.

- [ ] **Step 4: Tag the milestone**

```bash
git tag plan-4-complete
git tag --list
```

Expected: tag list now includes `walking-skeleton-complete`, `plan-2-complete`, `plan-3-complete`, `plan-4-complete`.

---

## Self-review notes (for the writer of this plan, already verified)

- **Spec coverage:** plan-4 brainstorming targets — `set_layout` (Task 8), `save_layout` / `load_layout` / `list_layouts` (Task 8), History view (Task 13 + 14), `bind_key` / `unbind_key` (Task 12), `set_config` / `get_config` (Task 12), action registry (Task 11) + `list_actions` (Task 12), `list_bindings` (Task 12), layout switcher modal (Task 9), hot-reload bindings (Task 14's `_rebind_keys`). All covered.
- **Carry-overs:** conftest (Task 1), queue_send (Task 2), prune _send_tasks (Task 3), spawn_agent schema (Task 4) — all done at the front of the plan.
- **Placeholder scan:** no "TODO" / "TBD" / "implement later". Every step has actual code or commands.
- **Type consistency:** `ConfigStore`, `KeyBinding`, `Config`, `UISection`, `ActionRegistry`, `ActionSpec`, `NamedLayoutsStore`, `LayoutApplied`, `LayoutFailed`, `HistoryScreen`, `LayoutSwitcherScreen`, `_rebind_keys`, `_orchestrator_apply_layout` — names used identically across all tasks.
- **Risk areas:**
  - Task 14's `_rebind_keys` depends on Textual's binding-refresh API. If `refresh_bindings()` doesn't exist or `_bindings` access doesn't work, the fallback (rebuild `BINDINGS` and trigger a full app refresh) is documented as STOP-and-report.
  - Task 9's `ListView.Selected` event surface may differ across Textual versions; documented as STOP-and-report.
  - Task 4's `spawn_agent` schema change: if the SDK rejects required-only schemas with optional fields, we keep the schema as-is and just document optional fields in the description; documented as STOP-and-report.
- **Tool count after plan 4:** orchestrator now has up to 17 tools (7 from plan 2/3 + 4 layout + 6 config). Children have 2 unchanged. The `_SPECS` table only holds the 7 base tools; layout and config tools are conditionally appended in the build functions because they require optional dependencies (App, ConfigStore, etc.). This is a pragmatic split — fully merging would require optional-dependency markers in `_ToolSpec`.
