# Permission Approval Modals Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the unconditional `permission_mode="bypassPermissions"` with a Textual modal flow that asks the user before any tool call. Permission prompts are **on by default** for both the orchestrator and child agents; the user opts out per launch with the `--bypass-permissions` CLI flag.

**Architecture:** `__main__.py` parses `--bypass-permissions`. `PatchfeldApp` constructs a `PermissionGrants` object iff bypass is *off* (the default), and passes it to both `AgentManager` (for children) and `OrchestratorSession` (for the orchestrator). Each session that has grants attaches a `can_use_tool` callback when it builds `ClaudeAgentOptions`; the callback consults `PermissionGrants` for standing rules, otherwise registers in a per-session `PermissionInbox`, publishes `PermissionRequested`, and `await`s. Two UI surfaces — a global `PermissionModal` and an inline `PermissionRequestBar` mounted inside `AgentTranscript` — both call `inbox.resolve(...)` to drain the same Future. The blocked session flips to a new `AgentState.AWAITING_PERMISSION` (orange) for the duration. Persistent "always allow for any future agent named X" decisions live in `.patchfeld/permission_grants.json`, owned by an isolated `permission_grants.py` module.

**Tech Stack:** Python 3.12, Textual 8.x, `claude_agent_sdk` (`CanUseTool`, `ToolPermissionContext`, `PermissionResultAllow`, `PermissionResultDeny`), `argparse`, `pytest-asyncio`.

**Important — overrides earlier design note:** An earlier draft of this plan (and the orchestrator's design-time confirmation) had the orchestrator stay always-bypass. The user explicitly overrode that: **both the orchestrator and child agents go through the modal flow by default.** Implementers reading this plan: there is no orchestrator carve-out.

---

## Section 1 — Design Decisions

### 1.1 Startup flag — CLI argument

**Choice:** Add `argparse` to `patchfeld/__main__.py`. Single boolean flag.

```bash
patchfeld                       # default: ask before every tool call
patchfeld --bypass-permissions  # today's behavior: bypass for everyone
```

**Rationale:**
- The user asked for a CLI argument; an env var would be a different surface than what was requested.
- Argument parsing belongs in `__main__.py`, not `App.__init__`. `App.__init__` is the test seam (every smoke test instantiates it directly), and tests should not have to mock argparse or sys.argv.
- Default ON ("ask") is the safer posture; advanced users and CI opt out per-launch.
- One flag, one bool — argparse stays minimal so the `__main__.py` diff is small.

### 1.2 Single source of truth: presence of `PermissionGrants`

Rather than threading a `bypass_permissions: bool` kwarg through three layers, **let the presence of a `PermissionGrants` object decide**:

- `AgentManager(permission_grants=None)` → bypass mode (`permission_mode="bypassPermissions"`, no callback).
- `AgentManager(permission_grants=<obj>)` → ask mode (drops bypass, attaches `can_use_tool`).

Same shape for `OrchestratorSession(permission_grants=...)`.

`PatchfeldApp.__init__(*, bypass_permissions: bool = False)` reads the bool, constructs `PermissionGrants` iff `bypass_permissions is False`, and passes the resulting `PermissionGrants | None` down. Default `False` matches argparse's no-flag semantics.

**Why this matters for the existing test suite:** every test that constructs `AgentManager` or `OrchestratorSession` does so without passing `permission_grants`. Those calls keep bypassing — zero test churn for an unrelated reason.

### 1.3 New `AgentState`: `AWAITING_PERMISSION`

The existing `WAITING` is overloaded for "blocked on an `ask_orchestrator` reply" (`RequestInbox`-driven). Permission requests are a *user* blocker, not an orchestrator blocker, and the AgentTable should distinguish them.

```python
class AgentState(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    WAITING = "waiting"
    AWAITING_PERMISSION = "awaiting_permission"
    DONE = "done"
    ERROR = "error"
```

Non-terminal. Status-cell color: **orange** (`"orange1"`; the existing yellow is reserved for `WAITING`). Composes with `WAITING` via twin pre-state snapshots in `AgentSession` so the two transitions can stack without one clobbering the other.

### 1.4 `PermissionInbox` — per-session

Sibling of `RequestInbox`, not an extension. `AgentManager` owns one per child; `OrchestratorSession` owns one for itself. Both hold `asyncio.Future[PermissionResult]`. Reasons to keep them separate:
- Different payload type (`PermissionResult` vs `str`).
- Different blocker semantics → different `AgentState` transition.
- They can be pending simultaneously (an `ask_orchestrator` and a permission request can overlap). Two inboxes keep counts independent.

### 1.5 `PermissionGrants` persistence

`(agent_name, tool_name) → "allow" | "deny"`, persisted at `.patchfeld/permission_grants.json`. Plus a session-only flavor that lives only in memory.

The orchestrator's `agent_name` is the literal string `"orchestrator"` (defined as `OrchestratorSession.AGENT_ID`). The wording in the modal uses that literal so "Always allow X for the orchestrator" reads naturally.

```json
{
  "version": 1,
  "grants": [
    {"agent_name": "orchestrator", "tool_name": "mcp__patchfeld_orchestrator__list_widgets", "behavior": "allow"},
    {"agent_name": "researcher",   "tool_name": "Read",                                     "behavior": "allow"},
    {"agent_name": "deleter",      "tool_name": "Bash",                                     "behavior": "deny"}
  ]
}
```

**Security tradeoff (sub-section)** — Keying persistent grants by `agent_name` is convenient (respawn the same name, reuse decisions) but surprising on rename collisions: today's "deleter" was sandboxed; if the user reuses the name with different intent next week, the old `Bash` deny still applies (which is on the safe side) and the old `Read` allow does too (which is less safe). A future revision could swap for an in-memory variant keyed by `agent_id` (scoped to one live spawn). All disk I/O is in this one module so the swap touches one file. Document this verbatim in the module docstring.

### 1.6 Modal vs inline — both surfaces hit the same `PermissionInbox`

The modal is `ModalScreen[None]` — it doesn't return the decision via `dismiss(...)`. It calls `inbox.resolve(...)` directly, broadcasts `PermissionResolved`, and advances its queue. The inline `PermissionRequestBar` does the same. The first surface to `resolve` wins; the second is a no-op (futures dedupe via `done()`).

Only one global modal can be open at a time. Additional concurrent requests stack in a `_queue` inside the modal; the modal cycles through them.

**The inline bar lives only in `AgentTranscript`.** The orchestrator panel (`OrchestratorChat`) is a different widget and does NOT get an inline bar — orchestrator requests go through the modal exclusively. (An `AgentTranscript` registered with `agent_id="orchestrator"` would render a bar; that's a degenerate case, harmless because the bar and modal coordinate via `PermissionResolved`.)

### 1.7 "Remember this choice" wording (orchestrator-aware)

The modal exposes scope explicitly so the user is never surprised:

- **Allow once** — resolve allow, no persistence.
- **Allow for the rest of this run** — in-memory grant on the `PermissionGrants` instance.
- **Always allow for any future agent named `<name>`** (or **for the orchestrator**) — write to disk.
- (Mirror trio for Deny.)

Label-construction code special-cases `agent_name == "orchestrator"` to read "Always allow X for the orchestrator" instead of "for any future agent named 'orchestrator'".

---

## Section 2 — File Map

**Create:**
- `patchfeld/agents/permission_inbox.py`
- `patchfeld/agents/permission_grants.py`
- `patchfeld/widgets/permission_modal.py`
- `patchfeld/widgets/permission_request_bar.py`
- `tests/test_permission_inbox.py`
- `tests/test_permission_grants.py`
- `tests/test_widget_permission_modal.py`
- `tests/test_widget_permission_request_bar.py`
- `tests/test_agent_manager_can_use_tool.py`
- `tests/test_orchestrator_session_can_use_tool.py`
- `tests/test_app_smoke_permission_modal.py`
- `tests/test_main_argparse.py`

**Modify:**
- `patchfeld/__main__.py` — argparse + `--bypass-permissions`.
- `patchfeld/app.py` — `bypass_permissions` kwarg, construct grants, push modal on first request.
- `patchfeld/agents/state.py` — add `AWAITING_PERMISSION`.
- `patchfeld/agents/session.py` — add `_mark_awaiting_permission` / `_mark_done_permission`.
- `patchfeld/agents/manager.py` — accept `permission_grants`, build per-child inbox + callback.
- `patchfeld/orchestrator/session.py` — accept `permission_grants`, build orchestrator inbox + callback.
- `patchfeld/events.py` — add `PermissionRequested`, `PermissionResolved`.
- `patchfeld/widgets/agent_table.py` — orange for `AWAITING_PERMISSION`.
- `patchfeld/widgets/agent_transcript.py` — mount `PermissionRequestBar` while pending.
- `tests/test_agent_state.py`, `tests/test_agent_table_widget.py`, `tests/test_agent_session.py`, `tests/test_agent_manager.py` — minor additions.

---

## Section 3 — Lifecycle Walk-Through (orchestrator + child)

1. User runs `patchfeld` (no flag). `__main__.py` parses argparse → `args.bypass_permissions=False` → `PatchfeldApp(bypass_permissions=False).run()`.
2. `PatchfeldApp.__init__` constructs `self._permission_grants = PermissionGrants(cwd=self.cwd)` and passes the object to both `AgentManager(permission_grants=...)` and `OrchestratorSession(permission_grants=...)`.
3. **Orchestrator path:** `OrchestratorSession._build_and_start_inner` checks `self._grants`:
   - Not None → drops `permission_mode="bypassPermissions"`, sets `can_use_tool=self._make_can_use_tool()`.
   - None (came up via `--bypass-permissions`) → keeps bypass.
4. **Child path:** `AgentManager._build_options` does the same.
5. The orchestrator (Claude) decides to call e.g. the `spawn_agent` MCP tool. The SDK invokes the orchestrator's `can_use_tool` callback with `tool_name="mcp__patchfeld_orchestrator__spawn_agent"`, identity `agent_name="orchestrator"`.
6. Callback consults `PermissionGrants.lookup(agent_name="orchestrator", tool_name=...)`:
   - Hit → return Allow/Deny without UI.
   - Miss → register in OrchestratorSession's `PermissionInbox`, publish `PermissionRequested(agent_id="orchestrator", agent_name="orchestrator", ...)`, `await`.
7. The App's first `PermissionRequested` handler pushes `PermissionModal`. The modal renders "agent: orchestrator · spawn_agent({…})" and the buttons. User clicks **Allow once**.
8. Modal calls `inbox.resolve(...)` (looking up the orchestrator's inbox), publishes `PermissionResolved`, advances its queue.
9. SDK proceeds to actually call `spawn_agent`, which spawns "researcher". The researcher's session is built with its own `can_use_tool` (because the manager has `permission_grants`). Its first tool call (e.g. `Read`) triggers another modal — `agent_name` is "researcher" this time.
10. **Status-cell visibility:** the orchestrator never appears in `AgentTable` (filtered out — see `agent_table.py:84` `if info.id == "orchestrator": continue`), so its `AWAITING_PERMISSION` state is invisible there. The child's row turns orange while it waits.

---

## Section 4 — Edge Cases (build tests against these)

### 4.1 Multiple simultaneous requests across sessions
Orchestrator and child both fire `can_use_tool` at once. Two `PermissionRequested` events fire. The `PermissionModal` is a single instance — it queues the second behind the first. Each session's `AgentTranscript` panel mounts its own bar (no contention).

### 4.2 Multiple simultaneous requests *from the same session*
The SDK can issue parallel tool calls. Each gets its own `request_id`; the inbox count stacks 0→1→2→1→0. `_mark_awaiting_permission` is idempotent; `_mark_done_permission` only restores the prior state when the count returns to zero.

### 4.3 Session killed mid-request
`AgentManager.kill` calls `inbox.cancel_all()` (new method) — every pending future is `Future.cancel()`ed. The closure's `try/except asyncio.CancelledError` returns `PermissionResultDeny(message="cancelled", interrupt=True)`. The closure also publishes a synthetic `PermissionResolved(behavior="cancelled")` so the UI surfaces remove their rows.

For the orchestrator, `OrchestratorSession.stop()` does the same.

### 4.4 User dismisses the modal without choosing (Esc)
**Decision:** Esc maps to "deny once" — never to "leave the request pending." Pending permission requests would block the session forever otherwise, which is especially bad for the orchestrator (would freeze the entire app). The modal's footer hint says `Esc = Deny`. Bar has no Esc semantics.

### 4.5 "Remember for this run" lifecycle
In-memory grants live on the `PermissionGrants` instance under `_session_grants`, keyed identically to disk grants but never written. `lookup` checks both, disk first. They evaporate when the app exits or `change_cwd` rebuilds the manager (which constructs a fresh `PermissionGrants`).

### 4.6 Persistence file corruption / missing
`PermissionGrants.__init__` catches `JSONDecodeError`/`OSError`/`KeyError` and starts with an empty rule set, logs the failure. Never blocks app startup.

### 4.7 `change_cwd` mid-pending-request
`change_cwd` shuts the orchestrator and manager down (which fires §4.3 for every pending request) and rebuilds them. `PatchfeldApp._permission_grants` is reconstructed at the new cwd so a different `permission_grants.json` may apply.

### 4.8 Resume of a persisted child agent
`AgentManager.resume` re-runs `_build_options`, which re-attaches `can_use_tool` with the resurrected info's name. Persistent grants for that name still apply.

### 4.9 `--bypass-permissions` matches today's behavior bit-for-bit
When the flag is passed: `bypass_permissions=True` → `_permission_grants=None` → no inboxes wired, no callback, no event subscriptions. Both orchestrator and children run with `permission_mode="bypassPermissions"`. The build-and-test outcome must be observably identical to the pre-feature codebase. Verify in Task 17.

---

## Section 5 — Tasks

> Granularity: each step is one atomic action (~2-5 min). Each task ends green (build + tests). Commit after each task.

---

### Task 1: Add `AWAITING_PERMISSION` to `AgentState`

**Files:**
- Modify: `patchfeld/agents/state.py`
- Test: `tests/test_agent_state.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_agent_state.py`:

```python
def test_awaiting_permission_is_a_distinct_state():
    from patchfeld.agents.state import AgentState
    assert AgentState.AWAITING_PERMISSION.value == "awaiting_permission"
    assert AgentState.AWAITING_PERMISSION != AgentState.WAITING


def test_awaiting_permission_is_not_terminal():
    from patchfeld.agents.state import AgentState
    assert AgentState.AWAITING_PERMISSION.is_terminal is False
```

- [ ] **Step 2: Run, verify FAIL**

Run: `pytest tests/test_agent_state.py::test_awaiting_permission_is_a_distinct_state -v`
Expected: `AttributeError: AWAITING_PERMISSION`.

- [ ] **Step 3: Implement**

Add to `patchfeld/agents/state.py`, between `WAITING` and `DONE`:

```python
class AgentState(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    WAITING = "waiting"
    AWAITING_PERMISSION = "awaiting_permission"
    DONE = "done"
    ERROR = "error"

    @property
    def is_terminal(self) -> bool:
        return self in (AgentState.DONE, AgentState.ERROR)
```

- [ ] **Step 4: Run, verify PASS**

Run: `pytest tests/test_agent_state.py -v`
Expected: pass. (`from_dict`/`to_dict` round-trip via `.value`, no other code changes needed.)

- [ ] **Step 5: Commit**

```bash
git add patchfeld/agents/state.py tests/test_agent_state.py
git commit -m "feat(state): add AWAITING_PERMISSION to AgentState"
```

---

### Task 2: Color `AWAITING_PERMISSION` orange in AgentTable

**Files:**
- Modify: `patchfeld/widgets/agent_table.py:18-24`
- Test: `tests/test_agent_table_widget.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_agent_table_widget.py`:

```python
@pytest.mark.asyncio
async def test_status_cell_uses_orange_for_awaiting_permission():
    from rich.text import Text
    bus = EventBus()
    app = _HostApp(bus)
    async with app.run_test() as pilot:
        await pilot.pause()
        info = _info(state=AgentState.AWAITING_PERMISSION)
        widget = app.query_one(AgentTable)
        cells = widget._render_cells(info)
        status_cell = cells[1]
        assert isinstance(status_cell, Text)
        assert status_cell.plain == "awaiting_permission"
        assert "orange" in str(status_cell.style).lower()
```

- [ ] **Step 2: Run, verify FAIL**

Run: `pytest tests/test_agent_table_widget.py::test_status_cell_uses_orange_for_awaiting_permission -v`
Expected: `assert "orange" in ""` (no style applied).

- [ ] **Step 3: Implement**

Edit `_STATUS_STYLES` in `patchfeld/widgets/agent_table.py`:

```python
_STATUS_STYLES: dict[_AgentState, str] = {
    _AgentState.IDLE: "dim",
    _AgentState.RUNNING: "green",
    _AgentState.WAITING: "yellow",
    _AgentState.AWAITING_PERMISSION: "orange1",
    _AgentState.DONE: "bold",
    _AgentState.ERROR: "red",
}
```

- [ ] **Step 4: Run, verify PASS**

Run: `pytest tests/test_agent_table_widget.py -v`
Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add patchfeld/widgets/agent_table.py tests/test_agent_table_widget.py
git commit -m "feat(agent-table): color AWAITING_PERMISSION orange"
```

---

### Task 3: Build `PermissionInbox` (mirror of `RequestInbox`)

**Files:**
- Create: `patchfeld/agents/permission_inbox.py`
- Test: `tests/test_permission_inbox.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_permission_inbox.py`:

```python
import asyncio

import pytest
from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

from patchfeld.agents.permission_inbox import PermissionInbox


async def _resolve_soon(inbox, rid, result):
    await asyncio.sleep(0)
    inbox.resolve(rid, result)


@pytest.mark.asyncio
async def test_register_and_resolve_round_trip():
    inbox = PermissionInbox()
    request_id = inbox.register(tool_name="Read", tool_input={"path": "x"})
    asyncio.create_task(_resolve_soon(inbox, request_id, PermissionResultAllow()))
    result = await inbox.wait(request_id, timeout_s=1.0)
    assert isinstance(result, PermissionResultAllow)


@pytest.mark.asyncio
async def test_pending_returns_open_requests():
    inbox = PermissionInbox()
    a = inbox.register(tool_name="Read", tool_input={})
    b = inbox.register(tool_name="Bash", tool_input={"cmd": "ls"})
    pending = inbox.pending()
    assert {p.request_id for p in pending} == {a, b}
    assert {p.tool_name for p in pending} == {"Read", "Bash"}


@pytest.mark.asyncio
async def test_on_pending_changed_fires_for_each_transition():
    counts: list[int] = []
    inbox = PermissionInbox(on_pending_changed=counts.append)
    rid = inbox.register(tool_name="Read", tool_input={})
    inbox.resolve(rid, PermissionResultAllow())
    await inbox.wait(rid, timeout_s=1.0)
    assert counts == [1, 0]


@pytest.mark.asyncio
async def test_cancel_all_marks_pending_futures_cancelled():
    inbox = PermissionInbox()
    rid = inbox.register(tool_name="Read", tool_input={})
    inbox.cancel_all()
    with pytest.raises(asyncio.CancelledError):
        await inbox.wait(rid, timeout_s=1.0)


@pytest.mark.asyncio
async def test_resolve_unknown_id_is_silently_ignored():
    inbox = PermissionInbox()
    inbox.resolve("nope", PermissionResultAllow())  # must not raise
```

- [ ] **Step 2: Run, verify FAIL**

Run: `pytest tests/test_permission_inbox.py -v`
Expected: `ModuleNotFoundError: patchfeld.agents.permission_inbox`.

- [ ] **Step 3: Implement**

Create `patchfeld/agents/permission_inbox.py`:

```python
import asyncio
import logging
import uuid
from dataclasses import dataclass
from typing import Callable

from claude_agent_sdk import PermissionResult

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PendingPermission:
    request_id: str
    tool_name: str
    tool_input: dict
    title: str | None = None
    description: str | None = None


class PermissionInbox:
    """Per-session registry of pending can_use_tool callbacks.

    Sibling to RequestInbox: same register/wait/resolve shape, different
    payload (PermissionResult vs str) and different blocker semantics — the
    AgentSession flips into AWAITING_PERMISSION while count > 0.

    `on_pending_changed`, if provided, is called synchronously after every
    transition that changes the pending count.
    """

    def __init__(
        self,
        *,
        on_pending_changed: Callable[[int], None] | None = None,
    ) -> None:
        self._records: dict[str, PendingPermission] = {}
        self._futures: dict[str, asyncio.Future] = {}
        self._on_pending_changed = on_pending_changed

    def register(
        self,
        *,
        tool_name: str,
        tool_input: dict,
        title: str | None = None,
        description: str | None = None,
    ) -> str:
        request_id = uuid.uuid4().hex[:12]
        loop = asyncio.get_running_loop()
        self._futures[request_id] = loop.create_future()
        self._records[request_id] = PendingPermission(
            request_id=request_id, tool_name=tool_name, tool_input=tool_input,
            title=title, description=description,
        )
        self._notify()
        return request_id

    def resolve(self, request_id: str, result: PermissionResult) -> None:
        future = self._futures.get(request_id)
        if future is not None and not future.done():
            future.set_result(result)

    async def wait(self, request_id: str, *, timeout_s: float) -> PermissionResult:
        future = self._futures.get(request_id)
        if future is None:
            raise KeyError(f"unknown request_id: {request_id}")
        try:
            return await asyncio.wait_for(future, timeout=timeout_s)
        finally:
            self._futures.pop(request_id, None)
            self._records.pop(request_id, None)
            self._notify()

    def cancel_all(self) -> None:
        """Cancel every pending future. Used by AgentManager.kill /
        OrchestratorSession.stop."""
        for fut in self._futures.values():
            if not fut.done():
                fut.cancel()

    def pending(self) -> list[PendingPermission]:
        return list(self._records.values())

    def _notify(self) -> None:
        if self._on_pending_changed is None:
            return
        try:
            self._on_pending_changed(len(self._futures))
        except Exception:
            log.exception("PermissionInbox.on_pending_changed handler raised")
```

- [ ] **Step 4: Run, verify PASS**

Run: `pytest tests/test_permission_inbox.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add patchfeld/agents/permission_inbox.py tests/test_permission_inbox.py
git commit -m "feat(permissions): add PermissionInbox per-session queue"
```

---

### Task 4: Build `PermissionGrants` persistence module

**Files:**
- Create: `patchfeld/agents/permission_grants.py`
- Test: `tests/test_permission_grants.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_permission_grants.py`:

```python
from pathlib import Path

import pytest

from patchfeld.agents.permission_grants import PermissionGrants


def test_lookup_returns_none_when_file_missing(tmp_path: Path):
    grants = PermissionGrants(cwd=tmp_path)
    assert grants.lookup(agent_name="r", tool_name="Read") is None


def test_remember_persists_to_disk_and_round_trips(tmp_path: Path):
    grants = PermissionGrants(cwd=tmp_path)
    grants.remember(agent_name="researcher", tool_name="Read", behavior="allow")
    fresh = PermissionGrants(cwd=tmp_path)
    assert fresh.lookup(agent_name="researcher", tool_name="Read") == "allow"


def test_remember_session_only_does_not_write_disk(tmp_path: Path):
    grants = PermissionGrants(cwd=tmp_path)
    grants.remember(
        agent_name="researcher", tool_name="Read", behavior="allow",
        scope="session",
    )
    assert grants.lookup(agent_name="researcher", tool_name="Read") == "allow"
    fresh = PermissionGrants(cwd=tmp_path)
    assert fresh.lookup(agent_name="researcher", tool_name="Read") is None


def test_disk_overrides_take_precedence_over_session(tmp_path: Path):
    grants = PermissionGrants(cwd=tmp_path)
    grants.remember(agent_name="r", tool_name="Read", behavior="deny", scope="persistent")
    grants.remember(agent_name="r", tool_name="Read", behavior="allow", scope="session")
    assert grants.lookup(agent_name="r", tool_name="Read") == "deny"


def test_orchestrator_grants_round_trip(tmp_path: Path):
    # Same shape works for the orchestrator's pseudo-agent name.
    grants = PermissionGrants(cwd=tmp_path)
    grants.remember(
        agent_name="orchestrator",
        tool_name="mcp__patchfeld_orchestrator__list_widgets",
        behavior="allow",
    )
    fresh = PermissionGrants(cwd=tmp_path)
    assert fresh.lookup(
        agent_name="orchestrator",
        tool_name="mcp__patchfeld_orchestrator__list_widgets",
    ) == "allow"


def test_clear_wipes_disk(tmp_path: Path):
    grants = PermissionGrants(cwd=tmp_path)
    grants.remember(agent_name="r", tool_name="Read", behavior="allow")
    grants.clear()
    fresh = PermissionGrants(cwd=tmp_path)
    assert fresh.lookup(agent_name="r", tool_name="Read") is None


def test_corrupt_file_starts_empty(tmp_path: Path):
    (tmp_path / ".patchfeld").mkdir()
    (tmp_path / ".patchfeld" / "permission_grants.json").write_text("not json")
    grants = PermissionGrants(cwd=tmp_path)  # must not raise
    assert grants.lookup(agent_name="r", tool_name="Read") is None
```

- [ ] **Step 2: Run, verify FAIL**

Run: `pytest tests/test_permission_grants.py -v`
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

Create `patchfeld/agents/permission_grants.py`:

```python
"""Persistent + session-scoped grant rules for tool permissions.

DESIGN TRADEOFF (revisit when convenient):
This module keys persistent grants by ``(agent_name, tool_name)``. The
agent_name is the literal "orchestrator" for the user's main session and
the user-supplied name for child agents. Respawning a child of the same
name reuses prior decisions — convenient in the common case, surprising
if a name is reused with different intent.

A future revision could swap the disk-backed lookup for an in-memory one
keyed by ``agent_id`` (scoped to one live spawn). The interface here is
intentionally narrow so the swap touches only this file.
"""

import json
import logging
from pathlib import Path
from typing import Literal

from patchfeld.persistence.atomic import write_json_atomic
from patchfeld.persistence.paths import project_state_dir

log = logging.getLogger(__name__)

Behavior = Literal["allow", "deny"]
Scope = Literal["persistent", "session"]


def _grants_path(cwd: Path) -> Path:
    return project_state_dir(cwd) / "permission_grants.json"


class PermissionGrants:
    """Disk-backed allow/deny rules keyed by (agent_name, tool_name).

    `remember(scope="session")` rules live in-memory only and evaporate on
    process exit. `remember(scope="persistent")` rules are serialized to
    `<cwd>/.patchfeld/permission_grants.json`.
    """

    def __init__(self, *, cwd: Path) -> None:
        self._cwd = Path(cwd)
        self._disk: dict[tuple[str, str], Behavior] = {}
        self._session: dict[tuple[str, str], Behavior] = {}
        self._load_disk()

    def lookup(self, *, agent_name: str, tool_name: str) -> Behavior | None:
        # Disk wins over session — disk represents an explicit "always" the
        # user chose earlier, session is "for this run." If both exist, the
        # persistent decision is more authoritative.
        key = (agent_name, tool_name)
        return self._disk.get(key) or self._session.get(key)

    def remember(
        self,
        *,
        agent_name: str,
        tool_name: str,
        behavior: Behavior,
        scope: Scope = "persistent",
    ) -> None:
        key = (agent_name, tool_name)
        if scope == "persistent":
            self._disk[key] = behavior
            self._write_disk()
        else:
            self._session[key] = behavior

    def clear(self) -> None:
        self._disk.clear()
        self._session.clear()
        try:
            _grants_path(self._cwd).unlink()
        except FileNotFoundError:
            pass

    def _load_disk(self) -> None:
        path = _grants_path(self._cwd)
        if not path.exists():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            for entry in raw.get("grants", []):
                key = (entry["agent_name"], entry["tool_name"])
                self._disk[key] = entry["behavior"]
        except (json.JSONDecodeError, OSError, KeyError, TypeError):
            log.exception("permission_grants.json unreadable; starting empty")
            self._disk.clear()

    def _write_disk(self) -> None:
        data = {
            "version": 1,
            "grants": [
                {"agent_name": a, "tool_name": t, "behavior": b}
                for (a, t), b in sorted(self._disk.items())
            ],
        }
        write_json_atomic(_grants_path(self._cwd), data)
```

- [ ] **Step 4: Run, verify PASS**

Run: `pytest tests/test_permission_grants.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add patchfeld/agents/permission_grants.py tests/test_permission_grants.py
git commit -m "feat(permissions): add PermissionGrants disk + session store"
```

---

### Task 5: Add `PermissionRequested` / `PermissionResolved` events

**Files:**
- Modify: `patchfeld/events.py`
- Test: `tests/test_events.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_events.py`:

```python
def test_permission_request_events_carry_required_fields():
    from patchfeld.events import PermissionRequested, PermissionResolved
    req = PermissionRequested(
        agent_id="a1", agent_name="researcher",
        request_id="r1", tool_name="Read", tool_input={"path": "x"},
        title=None, description=None,
    )
    assert req.agent_id == "a1"
    assert req.tool_name == "Read"

    res = PermissionResolved(
        agent_id="a1", request_id="r1", behavior="allow",
    )
    assert res.behavior == "allow"
```

- [ ] **Step 2: Run, verify FAIL**

Run: `pytest tests/test_events.py::test_permission_request_events_carry_required_fields -v`
Expected: `ImportError: cannot import name 'PermissionRequested'`.

- [ ] **Step 3: Implement**

In `patchfeld/events.py`, after the existing `AgentRequestedUserInput` block:

```python
@dataclass(frozen=True)
class PermissionRequested:
    """A session's SDK called can_use_tool. The session is blocked
    awaiting a user decision via the global modal or the per-agent
    transcript bar. agent_id == "orchestrator" identifies the orchestrator's
    own request."""
    agent_id: str
    agent_name: str
    request_id: str
    tool_name: str
    tool_input: dict
    title: str | None = None
    description: str | None = None


@dataclass(frozen=True)
class PermissionResolved:
    """A pending permission request was answered. Behavior is a string
    (`"allow"` | `"deny"` | `"cancelled"`) for serialization friendliness."""
    agent_id: str
    request_id: str
    behavior: str
```

- [ ] **Step 4: Run, verify PASS**

Run: `pytest tests/test_events.py -v`

- [ ] **Step 5: Commit**

```bash
git add patchfeld/events.py tests/test_events.py
git commit -m "feat(events): add PermissionRequested/Resolved"
```

---

### Task 6: Add `_mark_awaiting_permission` / `_mark_done_permission` to `AgentSession`

**Files:**
- Modify: `patchfeld/agents/session.py`
- Test: `tests/test_agent_session.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_agent_session.py`:

```python
@pytest.mark.asyncio
async def test_mark_awaiting_permission_flips_state_and_restores():
    from pathlib import Path
    from patchfeld.agents.session import AgentSession
    from patchfeld.agents.state import AgentInfo, AgentState
    from patchfeld.events import EventBus
    from patchfeld.agents.fake_sdk_adapter import FakeSDKAdapter
    from patchfeld.persistence.transcript_store import AgentTranscript

    info = AgentInfo(id="x", name="x", cwd="/tmp", started_at=0.0)
    info.state = AgentState.RUNNING
    session = AgentSession(
        info=info,
        adapter=FakeSDKAdapter(scripts=[]),
        transcript=AgentTranscript(cwd=Path("/tmp"), agent_id="x"),
        bus=EventBus(),
    )

    session._mark_awaiting_permission()
    assert info.state == AgentState.AWAITING_PERMISSION

    session._mark_done_permission()
    assert info.state == AgentState.RUNNING


@pytest.mark.asyncio
async def test_mark_awaiting_permission_stacked_with_waiting_restores_correctly():
    from pathlib import Path
    from patchfeld.agents.session import AgentSession
    from patchfeld.agents.state import AgentInfo, AgentState
    from patchfeld.events import EventBus
    from patchfeld.agents.fake_sdk_adapter import FakeSDKAdapter
    from patchfeld.persistence.transcript_store import AgentTranscript

    info = AgentInfo(id="x", name="x", cwd="/tmp", started_at=0.0)
    info.state = AgentState.RUNNING
    session = AgentSession(
        info=info,
        adapter=FakeSDKAdapter(scripts=[]),
        transcript=AgentTranscript(cwd=Path("/tmp"), agent_id="x"),
        bus=EventBus(),
    )

    session._mark_awaiting_permission()
    assert info.state == AgentState.AWAITING_PERMISSION

    session._mark_waiting()
    assert info.state == AgentState.WAITING

    session._mark_unwaiting()
    assert info.state == AgentState.AWAITING_PERMISSION

    session._mark_done_permission()
    assert info.state == AgentState.RUNNING
```

- [ ] **Step 2: Run, verify FAIL**

Run: `pytest tests/test_agent_session.py::test_mark_awaiting_permission_flips_state_and_restores -v`
Expected: `AttributeError: ...has no attribute '_mark_awaiting_permission'`.

- [ ] **Step 3: Implement**

In `patchfeld/agents/session.py`:

(a) In `__init__`, add `self._pre_perm_state: AgentState | None = None` next to `_pre_wait_state`.

(b) Add helpers below `_mark_unwaiting`:

```python
    def _mark_awaiting_permission(self) -> None:
        """Enter AWAITING_PERMISSION, snapshotting the prior state.

        Idempotent. Composes with _mark_waiting: a session can transition
        RUNNING → AWAITING_PERMISSION → WAITING → AWAITING_PERMISSION
        → RUNNING and end up where it started. Skipped if terminal.
        """
        if self.info.state.is_terminal:
            return
        if self.info.state == AgentState.AWAITING_PERMISSION:
            return
        self._pre_perm_state = self.info.state
        self._set_state(AgentState.AWAITING_PERMISSION)

    def _mark_done_permission(self) -> None:
        """Exit AWAITING_PERMISSION, restoring the pre-permission state."""
        if self.info.state != AgentState.AWAITING_PERMISSION:
            self._pre_perm_state = None
            return
        target = self._pre_perm_state or AgentState.RUNNING
        self._pre_perm_state = None
        if target.is_terminal:
            return
        self._set_state(target)
```

- [ ] **Step 4: Run, verify PASS**

Run: `pytest tests/test_agent_session.py -v`

- [ ] **Step 5: Commit**

```bash
git add patchfeld/agents/session.py tests/test_agent_session.py
git commit -m "feat(session): add awaiting-permission state transitions"
```

---

### Task 7: Wire `PermissionInbox` into `AgentManager` (no callback yet)

This task constructs the inbox per child and exposes `get_permission_inbox()`. It does NOT yet plumb `can_use_tool` — that's Task 8.

**Files:**
- Modify: `patchfeld/agents/manager.py`
- Test: `tests/test_agent_manager.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_agent_manager.py`:

```python
@pytest.mark.asyncio
async def test_get_permission_inbox_returns_inbox_per_agent(tmp_path: Path):
    from patchfeld.agents.permission_inbox import PermissionInbox
    manager = AgentManager(
        cwd=tmp_path,
        bus=EventBus(),
        adapter_factory=lambda: FakeSDKAdapter(scripts=[_ok_script()]),
    )
    aid = await manager.spawn(name="r", prompt="hi")
    await manager.wait_idle(aid)

    inbox = manager.get_permission_inbox(aid)
    assert isinstance(inbox, PermissionInbox)
    assert manager.get_permission_inbox(aid) is inbox
    assert manager.get_permission_inbox("nope") is None


@pytest.mark.asyncio
async def test_permission_inbox_register_flips_state_to_awaiting_permission(
    tmp_path: Path,
):
    from patchfeld.agents.state import AgentState
    bus = EventBus()
    manager = AgentManager(
        cwd=tmp_path,
        bus=bus,
        adapter_factory=lambda: FakeSDKAdapter(scripts=[_ok_script()]),
    )
    aid = await manager.spawn(name="r", prompt="hi")
    await manager.wait_idle(aid)

    session = manager.get_session(aid)
    session.info.state = AgentState.RUNNING
    inbox = manager.get_permission_inbox(aid)
    rid = inbox.register(tool_name="Read", tool_input={})
    assert session.info.state == AgentState.AWAITING_PERMISSION

    from claude_agent_sdk import PermissionResultAllow
    inbox.resolve(rid, PermissionResultAllow())
    await inbox.wait(rid, timeout_s=1.0)
    assert session.info.state == AgentState.RUNNING
```

- [ ] **Step 2: Run, verify FAIL**

Expected: `AttributeError: ...has no attribute 'get_permission_inbox'`.

- [ ] **Step 3: Implement**

In `patchfeld/agents/manager.py`:

(a) Add import: `from patchfeld.agents.permission_inbox import PermissionInbox`.

(b) In `__init__`, add `self._perm_inboxes: dict[str, PermissionInbox] = {}` next to `self._inboxes`.

(c) In `_build_session`, after the existing `RequestInbox` setup, add:

```python
        def _on_perm_changed(count: int, _session=session) -> None:
            if count > 0:
                _session._mark_awaiting_permission()
            else:
                _session._mark_done_permission()

        self._perm_inboxes[info.id] = PermissionInbox(
            on_pending_changed=_on_perm_changed,
        )
```

(d) Add the public accessor below `get_inbox`:

```python
    def get_permission_inbox(self, agent_id: str) -> PermissionInbox | None:
        return self._perm_inboxes.get(agent_id)
```

(e) In `kill`, after `self._inboxes.pop(...)`, add:

```python
        perm_inbox = self._perm_inboxes.pop(agent_id, None)
        if perm_inbox is not None:
            perm_inbox.cancel_all()
```

- [ ] **Step 4: Run, verify PASS**

Run: `pytest tests/test_agent_manager.py -v`

- [ ] **Step 5: Commit**

```bash
git add patchfeld/agents/manager.py tests/test_agent_manager.py
git commit -m "feat(manager): construct PermissionInbox per child agent"
```

---

### Task 8: Plumb `can_use_tool` callback in `AgentManager._build_options`

The presence of a `permission_grants` instance is the gate. No new bool kwarg.

**Files:**
- Modify: `patchfeld/agents/manager.py:113-141` and `__init__`
- Test: `tests/test_agent_manager_can_use_tool.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_agent_manager_can_use_tool.py`:

```python
import asyncio
from pathlib import Path

import pytest
from claude_agent_sdk import (
    PermissionResultAllow, PermissionResultDeny, ToolPermissionContext,
)

from patchfeld.agents.fake_sdk_adapter import FakeSDKAdapter
from patchfeld.agents.manager import AgentManager
from patchfeld.agents.permission_grants import PermissionGrants
from patchfeld.events import EventBus, PermissionRequested


def _ok_script():
    from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock
    return [
        AssistantMessage(content=[TextBlock(text="done")], model="m"),
        ResultMessage(
            subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
            num_turns=1, session_id="s", total_cost_usd=0.0,
            usage={"input_tokens": 1, "output_tokens": 1}, result="done",
        ),
    ]


@pytest.mark.asyncio
async def test_no_grants_keeps_bypass_permissions(tmp_path: Path):
    # Default constructor (no permission_grants kwarg) preserves today's
    # behavior — bypass for every child.
    manager = AgentManager(
        cwd=tmp_path, bus=EventBus(),
        adapter_factory=lambda: FakeSDKAdapter(scripts=[_ok_script()]),
    )
    aid = await manager.spawn(name="r", prompt="hi")
    info = next(i for i in manager.list_infos() if i.id == aid)
    opts = manager._build_options(info)
    assert opts.permission_mode == "bypassPermissions"
    assert opts.can_use_tool is None


@pytest.mark.asyncio
async def test_grants_provided_swaps_bypass_for_can_use_tool(tmp_path: Path):
    manager = AgentManager(
        cwd=tmp_path, bus=EventBus(),
        adapter_factory=lambda: FakeSDKAdapter(scripts=[_ok_script()]),
        permission_grants=PermissionGrants(cwd=tmp_path),
    )
    aid = await manager.spawn(name="r", prompt="hi")
    info = next(i for i in manager.list_infos() if i.id == aid)
    opts = manager._build_options(info)
    assert opts.permission_mode is None
    assert callable(opts.can_use_tool)


@pytest.mark.asyncio
async def test_callback_short_circuits_on_persistent_grant(tmp_path: Path):
    grants = PermissionGrants(cwd=tmp_path)
    grants.remember(agent_name="r", tool_name="Read", behavior="allow")
    bus = EventBus()
    requests: list[PermissionRequested] = []
    bus.subscribe(PermissionRequested, requests.append)

    manager = AgentManager(
        cwd=tmp_path, bus=bus,
        adapter_factory=lambda: FakeSDKAdapter(scripts=[_ok_script()]),
        permission_grants=grants,
    )
    aid = await manager.spawn(name="r", prompt="hi")
    info = next(i for i in manager.list_infos() if i.id == aid)
    callback = manager._build_options(info).can_use_tool

    ctx = ToolPermissionContext(tool_use_id="t1")
    result = await callback("Read", {"path": "x"}, ctx)
    assert isinstance(result, PermissionResultAllow)
    assert requests == []  # short-circuited; no UI involved


@pytest.mark.asyncio
async def test_callback_publishes_permission_requested_when_no_grant(tmp_path: Path):
    bus = EventBus()
    requests: list[PermissionRequested] = []
    bus.subscribe(PermissionRequested, requests.append)
    grants = PermissionGrants(cwd=tmp_path)

    manager = AgentManager(
        cwd=tmp_path, bus=bus,
        adapter_factory=lambda: FakeSDKAdapter(scripts=[_ok_script()]),
        permission_grants=grants,
    )
    aid = await manager.spawn(name="r", prompt="hi")
    info = next(i for i in manager.list_infos() if i.id == aid)
    callback = manager._build_options(info).can_use_tool
    ctx = ToolPermissionContext(tool_use_id="t1")

    async def driver():
        await asyncio.sleep(0)
        inbox = manager.get_permission_inbox(aid)
        pending = inbox.pending()
        assert pending and pending[0].tool_name == "Read"
        inbox.resolve(pending[0].request_id, PermissionResultAllow())

    driver_task = asyncio.create_task(driver())
    result = await callback("Read", {"path": "x"}, ctx)
    await driver_task

    assert isinstance(result, PermissionResultAllow)
    assert requests and requests[0].tool_name == "Read"
    assert requests[0].agent_id == aid
    assert requests[0].agent_name == "r"


@pytest.mark.asyncio
async def test_callback_returns_deny_when_inbox_cancelled(tmp_path: Path):
    bus = EventBus()
    grants = PermissionGrants(cwd=tmp_path)
    manager = AgentManager(
        cwd=tmp_path, bus=bus,
        adapter_factory=lambda: FakeSDKAdapter(scripts=[_ok_script()]),
        permission_grants=grants,
    )
    aid = await manager.spawn(name="r", prompt="hi")
    info = next(i for i in manager.list_infos() if i.id == aid)
    callback = manager._build_options(info).can_use_tool
    ctx = ToolPermissionContext(tool_use_id="t1")

    async def killer():
        await asyncio.sleep(0)
        manager.get_permission_inbox(aid).cancel_all()

    asyncio.create_task(killer())
    result = await callback("Read", {"path": "x"}, ctx)
    assert isinstance(result, PermissionResultDeny)
```

- [ ] **Step 2: Run, verify FAIL**

Run: `pytest tests/test_agent_manager_can_use_tool.py -v`
Expected: failures (`AgentManager.__init__() got an unexpected keyword argument 'permission_grants'`).

- [ ] **Step 3: Implement**

In `patchfeld/agents/manager.py`:

(a) Update imports:

```python
import asyncio

from claude_agent_sdk import (
    ClaudeAgentOptions,
    PermissionResultAllow,
    PermissionResultDeny,
    ToolPermissionContext,
)

from patchfeld.agents.permission_grants import PermissionGrants
from patchfeld.agents.permission_inbox import PermissionInbox
from patchfeld.events import (
    AgentArchiveChanged,
    AgentSpawned,
    AgentStateChanged,
    DirectMessageToAgent,
    EventBus,
    PermissionRequested,
    PermissionResolved,
)
```

(b) Update `__init__` signature:

```python
    def __init__(
        self,
        *,
        cwd: Path,
        bus: EventBus,
        adapter_factory: Callable[[], SDKAdapter],
        permission_grants: PermissionGrants | None = None,
    ) -> None:
        self._cwd = cwd
        self._bus = bus
        self._adapter_factory = adapter_factory
        self._grants = permission_grants  # presence == ask mode
        self._sessions: dict[str, AgentSession] = {}
        self._inboxes: dict[str, RequestInbox] = {}
        self._perm_inboxes: dict[str, PermissionInbox] = {}
        # ... rest of existing body
```

(c) Replace the body of `_build_options`. The old `permission_mode="bypassPermissions"` line is now conditional:

```python
    def _build_options(
        self, info: AgentInfo, *, resume_session_id: str | None = None,
    ) -> ClaudeAgentOptions:
        # Permission posture: presence of self._grants is the gate.
        #   - None  → permission_mode="bypassPermissions" (today's behavior;
        #     equivalent to launching with --bypass-permissions).
        #   - obj   → drop bypass, attach can_use_tool that consults the
        #     grants store first and falls back to the modal flow.
        # This applies symmetrically to OrchestratorSession (see §1.1/§1.2).
        child_mcp = build_child_mcp_server(
            agent_id=info.id, bus=self._bus, inbox=self._inboxes[info.id],
        )
        opts = info.spawn_options or {}
        kwargs: dict = {
            "cwd": opts.get("cwd") or info.cwd,
            "mcp_servers": {"patchfeld_child": child_mcp},
        }
        if self._grants is None:
            kwargs["permission_mode"] = "bypassPermissions"
        else:
            kwargs["can_use_tool"] = self._make_can_use_tool(
                agent_id=info.id, agent_name=info.name,
            )
        if opts.get("allowed_tools") is not None:
            kwargs["allowed_tools"] = opts["allowed_tools"]
        if opts.get("disallowed_tools") is not None:
            kwargs["disallowed_tools"] = opts["disallowed_tools"]
        if opts.get("model") is not None:
            kwargs["model"] = opts["model"]
        if opts.get("system_prompt") is not None:
            kwargs["system_prompt"] = opts["system_prompt"]
        if resume_session_id is not None:
            kwargs["resume"] = resume_session_id
        return ClaudeAgentOptions(**kwargs)

    def _make_can_use_tool(self, *, agent_id: str, agent_name: str):
        bus = self._bus
        grants = self._grants
        get_perm_inbox = self._perm_inboxes.get
        # 30 minutes — long enough to step away briefly, short enough that a
        # forgotten prompt doesn't strand the session forever.
        TIMEOUT_S = 30 * 60

        async def callback(
            tool_name: str,
            tool_input: dict,
            ctx: ToolPermissionContext,
        ):
            assert grants is not None  # invariant when callback is wired
            decision = grants.lookup(agent_name=agent_name, tool_name=tool_name)
            if decision == "allow":
                return PermissionResultAllow()
            if decision == "deny":
                return PermissionResultDeny(message="denied by saved rule")

            inbox = get_perm_inbox(agent_id)
            if inbox is None:
                return PermissionResultDeny(message="agent gone", interrupt=True)
            request_id = inbox.register(
                tool_name=tool_name, tool_input=tool_input,
                title=getattr(ctx, "title", None),
                description=getattr(ctx, "description", None),
            )
            bus.publish(PermissionRequested(
                agent_id=agent_id, agent_name=agent_name,
                request_id=request_id, tool_name=tool_name,
                tool_input=tool_input,
                title=getattr(ctx, "title", None),
                description=getattr(ctx, "description", None),
            ))
            try:
                result = await inbox.wait(request_id, timeout_s=TIMEOUT_S)
            except asyncio.CancelledError:
                bus.publish(PermissionResolved(
                    agent_id=agent_id, request_id=request_id,
                    behavior="cancelled",
                ))
                return PermissionResultDeny(message="cancelled", interrupt=True)
            except asyncio.TimeoutError:
                bus.publish(PermissionResolved(
                    agent_id=agent_id, request_id=request_id, behavior="deny",
                ))
                return PermissionResultDeny(message="timed out")
            bus.publish(PermissionResolved(
                agent_id=agent_id, request_id=request_id,
                behavior="allow" if isinstance(result, PermissionResultAllow) else "deny",
            ))
            return result

        return callback
```

- [ ] **Step 4: Run, verify PASS**

Run: `pytest tests/test_agent_manager.py tests/test_agent_manager_can_use_tool.py -v`
Expected: all pass. (Existing manager tests don't pass `permission_grants`, so they default to None → bypass → unchanged behavior.)

- [ ] **Step 5: Commit**

```bash
git add patchfeld/agents/manager.py tests/test_agent_manager_can_use_tool.py
git commit -m "feat(manager): wire can_use_tool when permission_grants is provided"
```

---

### Task 9: Wire `can_use_tool` into `OrchestratorSession`

The orchestrator gets the same treatment: when `permission_grants` is provided, drop `permission_mode="bypassPermissions"` and attach a callback that uses `OrchestratorSession.AGENT_ID` ("orchestrator") as the `agent_name`.

**Files:**
- Modify: `patchfeld/orchestrator/session.py`
- Test: `tests/test_orchestrator_session_can_use_tool.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_orchestrator_session_can_use_tool.py`:

```python
import asyncio
from pathlib import Path

import pytest
from claude_agent_sdk import (
    AssistantMessage, PermissionResultAllow, PermissionResultDeny,
    ResultMessage, TextBlock, ToolPermissionContext,
)

from patchfeld.agents.fake_sdk_adapter import FakeSDKAdapter
from patchfeld.agents.manager import AgentManager
from patchfeld.agents.permission_grants import PermissionGrants
from patchfeld.events import EventBus, PermissionRequested
from patchfeld.orchestrator.session import OrchestratorSession


def _ok():
    return [
        AssistantMessage(content=[TextBlock(text="hi")], model="m"),
        ResultMessage(
            subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
            num_turns=1, session_id="s", total_cost_usd=0.0,
            usage={"input_tokens": 1, "output_tokens": 1}, result="hi",
        ),
    ]


@pytest.mark.asyncio
async def test_no_grants_keeps_orchestrator_bypass(tmp_path: Path):
    bus = EventBus()
    manager = AgentManager(
        cwd=tmp_path, bus=bus,
        adapter_factory=lambda: FakeSDKAdapter(scripts=[_ok()]),
    )
    orch = OrchestratorSession(
        cwd=tmp_path, bus=bus, manager=manager,
        adapter=FakeSDKAdapter(scripts=[_ok()]),
    )
    await orch.start()
    # Inspect the options the inner session was started with by reaching
    # for the helper introduced below.
    assert orch.permission_grants is None
    assert orch.get_permission_inbox() is None
    await orch.stop()


@pytest.mark.asyncio
async def test_grants_provided_attaches_callback_and_inbox(tmp_path: Path):
    bus = EventBus()
    grants = PermissionGrants(cwd=tmp_path)
    manager = AgentManager(
        cwd=tmp_path, bus=bus,
        adapter_factory=lambda: FakeSDKAdapter(scripts=[_ok()]),
        permission_grants=grants,
    )
    orch = OrchestratorSession(
        cwd=tmp_path, bus=bus, manager=manager,
        adapter=FakeSDKAdapter(scripts=[_ok()]),
        permission_grants=grants,
    )
    await orch.start()
    assert orch.permission_grants is grants
    inbox = orch.get_permission_inbox()
    assert inbox is not None
    callback = orch._can_use_tool_callback
    assert callable(callback)

    # Short-circuit on persistent grant.
    grants.remember(
        agent_name="orchestrator",
        tool_name="mcp__patchfeld_orchestrator__list_widgets",
        behavior="allow",
    )
    ctx = ToolPermissionContext(tool_use_id="t1")
    result = await callback(
        "mcp__patchfeld_orchestrator__list_widgets", {}, ctx,
    )
    assert isinstance(result, PermissionResultAllow)
    await orch.stop()


@pytest.mark.asyncio
async def test_orchestrator_callback_publishes_event_with_orchestrator_identity(
    tmp_path: Path,
):
    bus = EventBus()
    requests: list[PermissionRequested] = []
    bus.subscribe(PermissionRequested, requests.append)
    grants = PermissionGrants(cwd=tmp_path)
    manager = AgentManager(
        cwd=tmp_path, bus=bus,
        adapter_factory=lambda: FakeSDKAdapter(scripts=[_ok()]),
        permission_grants=grants,
    )
    orch = OrchestratorSession(
        cwd=tmp_path, bus=bus, manager=manager,
        adapter=FakeSDKAdapter(scripts=[_ok()]),
        permission_grants=grants,
    )
    await orch.start()
    callback = orch._can_use_tool_callback
    ctx = ToolPermissionContext(tool_use_id="t1")

    async def driver():
        await asyncio.sleep(0)
        inbox = orch.get_permission_inbox()
        pending = inbox.pending()
        inbox.resolve(pending[0].request_id, PermissionResultAllow())

    asyncio.create_task(driver())
    result = await callback("Bash", {"cmd": "ls"}, ctx)
    assert isinstance(result, PermissionResultAllow)

    assert requests
    assert requests[0].agent_id == "orchestrator"
    assert requests[0].agent_name == "orchestrator"
    await orch.stop()
```

- [ ] **Step 2: Run, verify FAIL**

Expected: TypeError: unexpected kwarg `permission_grants` / AttributeError: `permission_grants`.

- [ ] **Step 3: Implement**

In `patchfeld/orchestrator/session.py`:

(a) Add imports:

```python
import asyncio

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    PermissionResultAllow,
    PermissionResultDeny,
    TextBlock,
    ToolPermissionContext,
    query as sdk_query,
)

from patchfeld.agents.permission_grants import PermissionGrants
from patchfeld.agents.permission_inbox import PermissionInbox
from patchfeld.events import (
    AgentMessageAppended,
    AgentNotifiedOrchestrator,
    AgentRequestedUserInput,
    AgentTokensTouched,
    EventBus,
    OpenResumePicker,
    OrchestratorReply,
    OrchestratorSessionSwitched,
    PermissionRequested,
    PermissionResolved,
    UserMessageToOrchestrator,
)
```

(b) Update the `__init__` signature, adding `permission_grants` and constructing the inbox + callback if provided:

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
        permission_grants: PermissionGrants | None = None,
    ) -> None:
        # ... existing body unchanged ...
        self._grants = permission_grants
        self._perm_inbox: PermissionInbox | None
        self._can_use_tool_callback = None
        if permission_grants is not None:
            self._perm_inbox = PermissionInbox(
                on_pending_changed=self._on_perm_changed,
            )
            self._can_use_tool_callback = self._make_can_use_tool()
        else:
            self._perm_inbox = None

    def _on_perm_changed(self, count: int) -> None:
        # Until _inner exists, no state to mark. After start(), forward to
        # _inner — same shape as the manager's _on_perm_changed.
        inner = getattr(self, "_inner", None)
        if inner is None:
            return
        if count > 0:
            inner._mark_awaiting_permission()
        else:
            inner._mark_done_permission()

    @property
    def permission_grants(self) -> PermissionGrants | None:
        return self._grants

    def get_permission_inbox(self) -> PermissionInbox | None:
        return self._perm_inbox

    def _make_can_use_tool(self):
        bus = self._bus
        grants = self._grants
        agent_name = self.AGENT_ID  # "orchestrator"
        get_inbox = lambda: self._perm_inbox
        TIMEOUT_S = 30 * 60

        async def callback(
            tool_name: str,
            tool_input: dict,
            ctx: ToolPermissionContext,
        ):
            assert grants is not None
            decision = grants.lookup(agent_name=agent_name, tool_name=tool_name)
            if decision == "allow":
                return PermissionResultAllow()
            if decision == "deny":
                return PermissionResultDeny(message="denied by saved rule")
            inbox = get_inbox()
            if inbox is None:
                return PermissionResultDeny(message="orchestrator gone", interrupt=True)
            request_id = inbox.register(
                tool_name=tool_name, tool_input=tool_input,
                title=getattr(ctx, "title", None),
                description=getattr(ctx, "description", None),
            )
            bus.publish(PermissionRequested(
                agent_id="orchestrator", agent_name=agent_name,
                request_id=request_id, tool_name=tool_name,
                tool_input=tool_input,
                title=getattr(ctx, "title", None),
                description=getattr(ctx, "description", None),
            ))
            try:
                result = await inbox.wait(request_id, timeout_s=TIMEOUT_S)
            except asyncio.CancelledError:
                bus.publish(PermissionResolved(
                    agent_id="orchestrator", request_id=request_id,
                    behavior="cancelled",
                ))
                return PermissionResultDeny(message="cancelled", interrupt=True)
            except asyncio.TimeoutError:
                bus.publish(PermissionResolved(
                    agent_id="orchestrator", request_id=request_id,
                    behavior="deny",
                ))
                return PermissionResultDeny(message="timed out")
            bus.publish(PermissionResolved(
                agent_id="orchestrator", request_id=request_id,
                behavior="allow" if isinstance(result, PermissionResultAllow) else "deny",
            ))
            return result

        return callback
```

(c) In `_build_and_start_inner`, replace the `options_kwargs` setup that currently always sets `permission_mode="bypassPermissions"`:

```python
        options_kwargs: dict = {
            "cwd": str(self._cwd),
            "mcp_servers": {"patchfeld_orchestrator": mcp_server},
        }
        if self._grants is None:
            options_kwargs["permission_mode"] = "bypassPermissions"
        else:
            options_kwargs["can_use_tool"] = self._can_use_tool_callback
        if resume is not None:
            options_kwargs["resume"] = resume
        if new_session_id is not None:
            options_kwargs["session_id"] = new_session_id
        if self._model is not None:
            options_kwargs["model"] = self._model
```

(d) In `stop()`, cancel pending permission futures:

```python
    async def stop(self) -> None:
        if self._perm_inbox is not None:
            self._perm_inbox.cancel_all()
        # ... existing stop body ...
```

- [ ] **Step 4: Run, verify PASS**

Run: `pytest tests/test_orchestrator_session_can_use_tool.py tests/test_orchestrator_session.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add patchfeld/orchestrator/session.py tests/test_orchestrator_session_can_use_tool.py
git commit -m "feat(orchestrator): wire can_use_tool when permission_grants is provided"
```

---

### Task 10: Add argparse to `__main__.py`

**Files:**
- Modify: `patchfeld/__main__.py`
- Test: `tests/test_main_argparse.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_main_argparse.py`:

```python
import sys
from unittest import mock

import pytest


def test_no_flag_passes_bypass_false(monkeypatch):
    from patchfeld import __main__ as main_mod

    captured: dict = {}
    class _StubApp:
        def __init__(self, *, bypass_permissions: bool = False) -> None:
            captured["bypass_permissions"] = bypass_permissions
        def run(self): captured["ran"] = True

    monkeypatch.setattr(main_mod, "PatchfeldApp", _StubApp)
    monkeypatch.setattr(sys, "argv", ["patchfeld"])
    rc = main_mod.main()
    assert rc == 0
    assert captured == {"bypass_permissions": False, "ran": True}


def test_bypass_flag_passes_bypass_true(monkeypatch):
    from patchfeld import __main__ as main_mod

    captured: dict = {}
    class _StubApp:
        def __init__(self, *, bypass_permissions: bool = False) -> None:
            captured["bypass_permissions"] = bypass_permissions
        def run(self): pass

    monkeypatch.setattr(main_mod, "PatchfeldApp", _StubApp)
    monkeypatch.setattr(sys, "argv", ["patchfeld", "--bypass-permissions"])
    main_mod.main()
    assert captured["bypass_permissions"] is True


def test_unknown_flag_exits_nonzero(monkeypatch, capsys):
    from patchfeld import __main__ as main_mod
    monkeypatch.setattr(sys, "argv", ["patchfeld", "--garbage"])
    with pytest.raises(SystemExit):
        main_mod.main()
```

- [ ] **Step 2: Run, verify FAIL**

Run: `pytest tests/test_main_argparse.py -v`
Expected: failures (`PatchfeldApp` doesn't accept `bypass_permissions` yet, and `__main__` doesn't parse args).

- [ ] **Step 3: Implement**

Replace `patchfeld/__main__.py` body:

```python
import argparse
import sys

from patchfeld.app import PatchfeldApp


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="patchfeld",
        description="Multi-agent Textual TUI for Claude Agent SDK.",
    )
    parser.add_argument(
        "--bypass-permissions",
        action="store_true",
        help=(
            "Run all sessions (orchestrator + child agents) with "
            "permission_mode=bypassPermissions. Default behavior is to "
            "ask for confirmation via a Textual modal before every "
            "tool call."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    PatchfeldApp(bypass_permissions=args.bypass_permissions).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run, verify PASS**

Run: `pytest tests/test_main_argparse.py -v`
Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add patchfeld/__main__.py tests/test_main_argparse.py
git commit -m "feat(cli): add --bypass-permissions flag to __main__"
```

---

### Task 11: Wire `bypass_permissions` through `PatchfeldApp`

**Files:**
- Modify: `patchfeld/app.py` (`__init__` + `change_cwd`)
- Test: `tests/test_app_smoke_permission_modal.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_app_smoke_permission_modal.py`:

```python
from pathlib import Path

import pytest

from patchfeld.app import PatchfeldApp


def test_default_constructs_grants_object(tmp_path: Path):
    app = PatchfeldApp(cwd=tmp_path, global_dir=tmp_path / "cfg")
    assert app._permission_grants is not None
    assert app.manager._grants is app._permission_grants
    assert app.orchestrator.permission_grants is app._permission_grants


def test_bypass_permissions_skips_grants(tmp_path: Path):
    app = PatchfeldApp(
        cwd=tmp_path, global_dir=tmp_path / "cfg",
        bypass_permissions=True,
    )
    assert app._permission_grants is None
    assert app.manager._grants is None
    assert app.orchestrator.permission_grants is None
```

- [ ] **Step 2: Run, verify FAIL**

Expected: `TypeError: __init__() got an unexpected keyword argument 'bypass_permissions'`.

- [ ] **Step 3: Implement**

In `patchfeld/app.py`:

(a) Add import: `from patchfeld.agents.permission_grants import PermissionGrants`.

(b) Update `PatchfeldApp.__init__` signature and grant-construction:

```python
    def __init__(
        self,
        *,
        cwd: Path | None = None,
        registry: WidgetRegistry | None = None,
        manager: AgentManager | None = None,
        orchestrator: OrchestratorSession | None = None,
        global_dir: Path | None = None,
        bypass_permissions: bool = False,
    ) -> None:
        super().__init__()
        # ... existing body up to the manager construction ...

        # Permission posture: a PermissionGrants object is constructed iff
        # bypass is OFF (the default). Both the manager and the orchestrator
        # consume the same object so disk-backed rules apply uniformly.
        self._bypass_permissions = bypass_permissions
        self._permission_grants = (
            None if bypass_permissions else PermissionGrants(cwd=self.cwd)
        )

        self.manager = manager or AgentManager(
            cwd=self.cwd,
            bus=self.event_bus,
            adapter_factory=RealSDKAdapter,
            permission_grants=self._permission_grants,
        )
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
            permission_grants=self._permission_grants,
        )
        self.orchestrator._auto_title_enabled = True
```

(c) In `change_cwd`, mirror the construction when rebuilding both:

```python
            self._permission_grants = (
                None if self._bypass_permissions else PermissionGrants(cwd=self.cwd)
            )
            self.manager = AgentManager(
                cwd=self.cwd, bus=self.event_bus,
                adapter_factory=RealSDKAdapter,
                permission_grants=self._permission_grants,
            )
            self.orchestrator = OrchestratorSession(
                cwd=self.cwd, bus=self.event_bus, manager=self.manager,
                apply_layout=self._orchestrator_apply_layout,
                layouts_store=self.layouts_store,
                themes_store=self.themes_store,
                config_store=self.config_store,
                actions=self.actions_registry,
                rebind_keys=self._rebind_keys,
                widget_registry=self.registry,
                current_layout=lambda: self._active_layout(),
                app=self,
                permission_grants=self._permission_grants,
            )
```

- [ ] **Step 4: Run, verify PASS**

Run: `pytest tests/test_app_smoke_permission_modal.py tests/test_app_smoke.py tests/test_app_change_cwd.py -v`
Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add patchfeld/app.py tests/test_app_smoke_permission_modal.py
git commit -m "feat(app): bypass_permissions kwarg gates PermissionGrants construction"
```

---

### Task 12: Build `PermissionModal`

**Files:**
- Create: `patchfeld/widgets/permission_modal.py`
- Test: `tests/test_widget_permission_modal.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_widget_permission_modal.py`:

```python
from pathlib import Path

import pytest
from textual.app import App, ComposeResult

from patchfeld.agents.permission_grants import PermissionGrants
from patchfeld.agents.permission_inbox import PermissionInbox
from patchfeld.events import EventBus, PermissionRequested
from patchfeld.widgets.permission_modal import PermissionModal


class _Host(App):
    def __init__(self, *, bus, inbox, grants, agent_name="researcher"):
        super().__init__()
        self.event_bus = bus
        self._inbox = inbox
        self._grants = grants
        self._agent_name = agent_name

    def compose(self) -> ComposeResult:
        from textual.widgets import Input
        yield Input()

    async def on_mount(self) -> None:
        await self.push_screen(PermissionModal(
            inbox_lookup=lambda aid: self._inbox,
            grants=self._grants,
        ))


def _request(rid="r1", aid="a1", agent_name="researcher", tool="Read"):
    return PermissionRequested(
        agent_id=aid, agent_name=agent_name, request_id=rid,
        tool_name=tool, tool_input={"path": "x"},
        title=f"Claude wants to {tool}", description=None,
    )


@pytest.mark.asyncio
async def test_modal_renders_pending_request(tmp_path: Path):
    bus = EventBus()
    inbox = PermissionInbox()
    grants = PermissionGrants(cwd=tmp_path)
    rid = inbox.register(tool_name="Read", tool_input={"path": "x"})
    app = _Host(bus=bus, inbox=inbox, grants=grants)
    async with app.run_test() as pilot:
        await pilot.pause()
        bus.publish(_request(rid=rid))
        await pilot.pause()
        assert app.screen._current_request is not None
        assert app.screen._current_request.tool_name == "Read"


@pytest.mark.asyncio
async def test_allow_once_resolves_inbox_with_allow(tmp_path: Path):
    from claude_agent_sdk import PermissionResultAllow
    bus = EventBus()
    inbox = PermissionInbox()
    rid = inbox.register(tool_name="Read", tool_input={})
    grants = PermissionGrants(cwd=tmp_path)

    app = _Host(bus=bus, inbox=inbox, grants=grants)
    async with app.run_test() as pilot:
        await pilot.pause()
        bus.publish(_request(rid=rid))
        await pilot.pause()
        await pilot.click("#allow-once")
        await pilot.pause()
    fut = inbox._futures.get(rid)
    assert fut is None or (fut.done() and isinstance(fut.result(), PermissionResultAllow))


@pytest.mark.asyncio
async def test_always_allow_for_named_agent_writes_disk(tmp_path: Path):
    bus = EventBus()
    inbox = PermissionInbox()
    rid = inbox.register(tool_name="Read", tool_input={})
    grants = PermissionGrants(cwd=tmp_path)

    app = _Host(bus=bus, inbox=inbox, grants=grants)
    async with app.run_test() as pilot:
        await pilot.pause()
        bus.publish(_request(rid=rid))
        await pilot.pause()
        await pilot.click("#allow-always")
        await pilot.pause()
    fresh = PermissionGrants(cwd=tmp_path)
    assert fresh.lookup(agent_name="researcher", tool_name="Read") == "allow"


@pytest.mark.asyncio
async def test_always_allow_for_orchestrator_writes_disk(tmp_path: Path):
    bus = EventBus()
    inbox = PermissionInbox()
    rid = inbox.register(tool_name="Bash", tool_input={"cmd": "ls"})
    grants = PermissionGrants(cwd=tmp_path)

    app = _Host(bus=bus, inbox=inbox, grants=grants, agent_name="orchestrator")
    async with app.run_test() as pilot:
        await pilot.pause()
        bus.publish(_request(rid=rid, agent_name="orchestrator", tool="Bash"))
        await pilot.pause()
        # The "always allow" button label should reference "the orchestrator".
        button = app.screen.query_one("#allow-always")
        assert "orchestrator" in str(button.label).lower()
        await pilot.click("#allow-always")
        await pilot.pause()
    fresh = PermissionGrants(cwd=tmp_path)
    assert fresh.lookup(agent_name="orchestrator", tool_name="Bash") == "allow"


@pytest.mark.asyncio
async def test_escape_denies_once(tmp_path: Path):
    from claude_agent_sdk import PermissionResultDeny
    bus = EventBus()
    inbox = PermissionInbox()
    rid = inbox.register(tool_name="Read", tool_input={})
    grants = PermissionGrants(cwd=tmp_path)

    app = _Host(bus=bus, inbox=inbox, grants=grants)
    async with app.run_test() as pilot:
        await pilot.pause()
        bus.publish(_request(rid=rid))
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
    fut = inbox._futures.get(rid)
    assert fut is None or (fut.done() and isinstance(fut.result(), PermissionResultDeny))


@pytest.mark.asyncio
async def test_modal_queues_second_request_until_first_resolves(tmp_path: Path):
    bus = EventBus()
    inbox = PermissionInbox()
    rid1 = inbox.register(tool_name="Read", tool_input={})
    rid2 = inbox.register(tool_name="Bash", tool_input={"cmd": "ls"})
    grants = PermissionGrants(cwd=tmp_path)

    app = _Host(bus=bus, inbox=inbox, grants=grants)
    async with app.run_test() as pilot:
        await pilot.pause()
        bus.publish(_request(rid=rid1))
        bus.publish(_request(rid=rid2))
        await pilot.pause()
        assert app.screen._current_request.request_id == rid1
        await pilot.click("#allow-once")
        await pilot.pause()
        assert app.screen._current_request.request_id == rid2
```

- [ ] **Step 2: Run, verify FAIL**

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

Create `patchfeld/widgets/permission_modal.py`:

```python
from typing import Callable

from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static

from patchfeld.agents.permission_grants import PermissionGrants
from patchfeld.agents.permission_inbox import PermissionInbox
from patchfeld.events import (
    EventBus, PermissionRequested, PermissionResolved,
)


_ORCHESTRATOR = "orchestrator"


def _scope_label_always_allow(*, agent_name: str, tool_name: str) -> str:
    if agent_name == _ORCHESTRATOR:
        return f"Always allow {tool_name} for the orchestrator"
    return f"Always allow {tool_name} for any future agent named {agent_name!r}"


def _scope_label_always_deny(*, agent_name: str, tool_name: str) -> str:
    if agent_name == _ORCHESTRATOR:
        return f"Always deny {tool_name} for the orchestrator"
    return f"Always deny {tool_name} for any future agent named {agent_name!r}"


class PermissionModal(ModalScreen[None]):
    """Global permission-prompt modal.

    Subscribes to PermissionRequested. While at least one request is
    pending, displays the head of the queue with Allow/Deny buttons +
    explicit-scope variants. Resolves directly via the session's
    PermissionInbox; uses the bus to receive new requests and to broadcast
    PermissionResolved so the per-agent transcript bar can clear its
    inline view.
    """

    DEFAULT_CSS = """
    PermissionModal { align: center middle; }
    PermissionModal > Vertical {
        width: 90; height: auto; padding: 1 2;
        background: $surface; border: round $warning;
    }
    PermissionModal #title { text-style: bold; }
    PermissionModal #buttons { height: 3; align-horizontal: center; }
    PermissionModal Button { margin: 0 1; }
    """

    BINDINGS = [Binding("escape", "deny_once", "deny once")]

    def __init__(
        self,
        *,
        inbox_lookup: Callable[[str], PermissionInbox | None],
        grants: PermissionGrants,
    ) -> None:
        super().__init__()
        self._inbox_lookup = inbox_lookup
        self._grants = grants
        self._queue: list[PermissionRequested] = []
        self._current_request: PermissionRequested | None = None
        self._unsub_req = lambda: None
        self._unsub_res = lambda: None

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Permission requested", id="title")
            yield Label("(no pending request)", id="prompt")
            yield Label("", id="agent")
            yield Label("", id="tool-args")
            with Horizontal(id="buttons"):
                yield Button("Allow once", id="allow-once", variant="success")
                yield Button(
                    "Allow for the rest of this run",
                    id="allow-session", variant="success",
                )
                yield Button("Always allow", id="allow-always", variant="success")
                yield Button("Deny once", id="deny-once", variant="error")
                yield Button("Always deny", id="deny-always", variant="error")

    def on_mount(self) -> None:
        bus: EventBus = self.app.event_bus
        self._unsub_req = bus.subscribe(PermissionRequested, self._on_request)
        self._unsub_res = bus.subscribe(PermissionResolved, self._on_resolved_elsewhere)

    def on_unmount(self) -> None:
        self._unsub_req()
        self._unsub_res()

    def _on_request(self, event: PermissionRequested) -> None:
        if self._current_request is None:
            self._current_request = event
            self._render_current()
        else:
            self._queue.append(event)

    def _on_resolved_elsewhere(self, event: PermissionResolved) -> None:
        if (self._current_request is not None
                and self._current_request.request_id == event.request_id):
            self._advance()
            return
        self._queue = [q for q in self._queue if q.request_id != event.request_id]

    def _render_current(self) -> None:
        req = self._current_request
        if req is None:
            return
        self.query_one("#prompt", Label).update(
            req.title or f"Allow {req.tool_name}?"
        )
        agent_label = (
            "agent: orchestrator" if req.agent_name == _ORCHESTRATOR
            else f"agent: {req.agent_name}"
        )
        self.query_one("#agent", Label).update(agent_label)
        self.query_one("#tool-args", Label).update(
            f"{req.tool_name}({_short_repr(req.tool_input)})"
        )
        self.query_one("#allow-always", Button).label = _scope_label_always_allow(
            agent_name=req.agent_name, tool_name=req.tool_name,
        )
        self.query_one("#deny-always", Button).label = _scope_label_always_deny(
            agent_name=req.agent_name, tool_name=req.tool_name,
        )

    def _advance(self) -> None:
        if self._queue:
            self._current_request = self._queue.pop(0)
            self._render_current()
        else:
            self._current_request = None
            self.query_one("#prompt", Label).update("(no pending request)")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if self._current_request is None:
            return
        bid = event.button.id
        if bid == "allow-once":
            self._resolve("allow", scope=None)
        elif bid == "allow-session":
            self._resolve("allow", scope="session")
        elif bid == "allow-always":
            self._resolve("allow", scope="persistent")
        elif bid == "deny-once":
            self._resolve("deny", scope=None)
        elif bid == "deny-always":
            self._resolve("deny", scope="persistent")

    def action_deny_once(self) -> None:
        if self._current_request is None:
            self.dismiss(None)
            return
        self._resolve("deny", scope=None)

    def _resolve(self, behavior: str, *, scope: str | None) -> None:
        req = self._current_request
        if req is None:
            return
        if scope is not None:
            self._grants.remember(
                agent_name=req.agent_name, tool_name=req.tool_name,
                behavior=behavior, scope=scope,
            )
        result = (
            PermissionResultAllow() if behavior == "allow"
            else PermissionResultDeny(message="user denied")
        )
        inbox = self._inbox_lookup(req.agent_id)
        if inbox is not None:
            inbox.resolve(req.request_id, result)
        self.app.event_bus.publish(PermissionResolved(
            agent_id=req.agent_id, request_id=req.request_id,
            behavior=behavior,
        ))
        self._advance()


def _short_repr(value: object, limit: int = 200) -> str:
    s = repr(value)
    return s if len(s) <= limit else s[: limit - 1] + "…"
```

- [ ] **Step 4: Run, verify PASS**

Run: `pytest tests/test_widget_permission_modal.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add patchfeld/widgets/permission_modal.py tests/test_widget_permission_modal.py
git commit -m "feat(modal): add global PermissionModal screen"
```

---

### Task 13: Build inline `PermissionRequestBar` for `AgentTranscript`

**Files:**
- Create: `patchfeld/widgets/permission_request_bar.py`
- Modify: `patchfeld/widgets/agent_transcript.py`
- Test: `tests/test_widget_permission_request_bar.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_widget_permission_request_bar.py`:

```python
from pathlib import Path

import pytest
from textual.app import App, ComposeResult

from patchfeld.agents.permission_grants import PermissionGrants
from patchfeld.agents.permission_inbox import PermissionInbox
from patchfeld.events import EventBus, PermissionRequested, PermissionResolved
from patchfeld.widgets.agent_transcript import AgentTranscript


class _Host(App):
    def __init__(self, *, bus, inboxes, grants):
        super().__init__()
        self.event_bus = bus
        self._inboxes = inboxes
        class _StubManager:
            def get_permission_inbox(_self, aid):
                return inboxes.get(aid)
        self.manager = _StubManager()
        self._permission_grants = grants

    def compose(self) -> ComposeResult:
        yield AgentTranscript(agent_id="a1", event_bus=self.event_bus)


def _request(rid="r1"):
    return PermissionRequested(
        agent_id="a1", agent_name="researcher", request_id=rid,
        tool_name="Read", tool_input={"path": "x"},
    )


@pytest.mark.asyncio
async def test_bar_appears_on_request_for_this_agent(tmp_path: Path):
    bus = EventBus()
    inbox = PermissionInbox()
    rid = inbox.register(tool_name="Read", tool_input={"path": "x"})
    grants = PermissionGrants(cwd=tmp_path)
    app = _Host(bus=bus, inboxes={"a1": inbox}, grants=grants)
    async with app.run_test() as pilot:
        await pilot.pause()
        bus.publish(_request(rid=rid))
        await pilot.pause()
        from patchfeld.widgets.permission_request_bar import PermissionRequestBar
        bars = app.query(PermissionRequestBar)
        assert len(bars) == 1


@pytest.mark.asyncio
async def test_bar_does_not_appear_for_other_agents(tmp_path: Path):
    bus = EventBus()
    other_inbox = PermissionInbox()
    rid = other_inbox.register(tool_name="Read", tool_input={})
    grants = PermissionGrants(cwd=tmp_path)
    app = _Host(bus=bus, inboxes={"a2": other_inbox}, grants=grants)
    async with app.run_test() as pilot:
        await pilot.pause()
        bus.publish(PermissionRequested(
            agent_id="a2", agent_name="other", request_id=rid,
            tool_name="Read", tool_input={},
        ))
        await pilot.pause()
        from patchfeld.widgets.permission_request_bar import PermissionRequestBar
        assert len(app.query(PermissionRequestBar)) == 0


@pytest.mark.asyncio
async def test_bar_allow_button_resolves_inbox(tmp_path: Path):
    from claude_agent_sdk import PermissionResultAllow
    bus = EventBus()
    inbox = PermissionInbox()
    rid = inbox.register(tool_name="Read", tool_input={})
    grants = PermissionGrants(cwd=tmp_path)
    app = _Host(bus=bus, inboxes={"a1": inbox}, grants=grants)
    async with app.run_test() as pilot:
        await pilot.pause()
        bus.publish(_request(rid=rid))
        await pilot.pause()
        await pilot.click("#bar-allow-once")
        await pilot.pause()
        fut = inbox._futures.get(rid)
        assert fut is None or (fut.done() and isinstance(fut.result(), PermissionResultAllow))


@pytest.mark.asyncio
async def test_bar_clears_when_resolution_comes_externally(tmp_path: Path):
    bus = EventBus()
    inbox = PermissionInbox()
    rid = inbox.register(tool_name="Read", tool_input={})
    grants = PermissionGrants(cwd=tmp_path)
    app = _Host(bus=bus, inboxes={"a1": inbox}, grants=grants)
    async with app.run_test() as pilot:
        await pilot.pause()
        bus.publish(_request(rid=rid))
        await pilot.pause()
        from patchfeld.widgets.permission_request_bar import PermissionRequestBar
        assert len(app.query(PermissionRequestBar)) == 1
        bus.publish(PermissionResolved(
            agent_id="a1", request_id=rid, behavior="allow",
        ))
        await pilot.pause()
        assert len(app.query(PermissionRequestBar)) == 0
```

- [ ] **Step 2: Run, verify FAIL**

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement the bar**

Create `patchfeld/widgets/permission_request_bar.py`:

```python
from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Button, Static

from patchfeld.events import PermissionRequested, PermissionResolved


class PermissionRequestBar(Horizontal):
    """A single inline approval row mounted at the top of an AgentTranscript
    while a permission request is pending for that agent.

    Clicks call into the agent's PermissionInbox via the App's manager;
    coordination with the global modal happens through PermissionResolved.
    """

    DEFAULT_CSS = """
    PermissionRequestBar {
        height: 3;
        background: $warning-darken-2;
        padding: 0 1;
    }
    PermissionRequestBar Static.label { width: 1fr; }
    PermissionRequestBar Button { margin: 0 1; }
    """

    def __init__(self, *, request: PermissionRequested) -> None:
        super().__init__()
        self._request = request

    @property
    def request_id(self) -> str:
        return self._request.request_id

    def compose(self) -> ComposeResult:
        req = self._request
        title = req.title or f"Allow {req.tool_name}?"
        yield Static(
            f"⚠ {title} — {req.tool_name}({_short(req.tool_input)})",
            classes="label",
        )
        yield Button("Allow", id="bar-allow-once", variant="success")
        yield Button("Always allow", id="bar-allow-always", variant="success")
        yield Button("Deny", id="bar-deny-once", variant="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        grants = getattr(self.app, "_permission_grants", None)
        if bid == "bar-allow-once":
            self._resolve("allow", scope=None, grants=grants)
        elif bid == "bar-allow-always":
            self._resolve("allow", scope="persistent", grants=grants)
        elif bid == "bar-deny-once":
            self._resolve("deny", scope=None, grants=grants)

    def _resolve(self, behavior: str, *, scope: str | None, grants) -> None:
        req = self._request
        if scope is not None and grants is not None:
            grants.remember(
                agent_name=req.agent_name, tool_name=req.tool_name,
                behavior=behavior, scope=scope,
            )
        manager = getattr(self.app, "manager", None)
        inbox = manager.get_permission_inbox(req.agent_id) if manager else None
        if inbox is not None:
            result = (
                PermissionResultAllow() if behavior == "allow"
                else PermissionResultDeny(message="user denied")
            )
            inbox.resolve(req.request_id, result)
        self.app.event_bus.publish(PermissionResolved(
            agent_id=req.agent_id, request_id=req.request_id,
            behavior=behavior,
        ))


def _short(value: object, limit: int = 80) -> str:
    s = repr(value)
    return s if len(s) <= limit else s[: limit - 1] + "…"
```

- [ ] **Step 4: Wire bar into `AgentTranscript`**

Edit `patchfeld/widgets/agent_transcript.py`:

```python
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Input

from patchfeld.events import (
    DirectMessageToAgent, EventBus,
    PermissionRequested, PermissionResolved,
)
from patchfeld.widgets.rich_transcript import RichTranscript


class AgentTranscript(Vertical):
    """Per-agent transcript panel: optional permission bar, transcript, input."""

    DEFAULT_CSS = """
    AgentTranscript {
        border: round $surface-lighten-2;
        padding: 0 1;
    }
    AgentTranscript > RichTranscript {
        height: 1fr;
    }
    AgentTranscript #transcript-input {
        dock: bottom;
        height: 3;
    }
    """

    def __init__(
        self,
        *,
        agent_id: str,
        event_bus: EventBus | None = None,
    ) -> None:
        super().__init__()
        self._agent_id = agent_id
        self._bus = event_bus
        self._unsub_perm_req = lambda: None
        self._unsub_perm_res = lambda: None

    def compose(self) -> ComposeResult:
        yield RichTranscript(agent_id=self._agent_id, event_bus=self._bus)
        yield Input(placeholder=f"Message {self._agent_id}…", id="transcript-input")

    def on_mount(self) -> None:
        bus = self._bus or getattr(self.app, "event_bus", None)
        if bus is None:
            return
        self._unsub_perm_req = bus.subscribe(PermissionRequested, self._on_perm_request)
        self._unsub_perm_res = bus.subscribe(PermissionResolved, self._on_perm_resolved)

    def on_unmount(self) -> None:
        self._unsub_perm_req()
        self._unsub_perm_res()

    def _on_perm_request(self, event: PermissionRequested) -> None:
        if event.agent_id != self._agent_id:
            return
        from patchfeld.widgets.permission_request_bar import PermissionRequestBar
        bar = PermissionRequestBar(request=event)
        self.mount(bar, before=self.query_one(RichTranscript))

    def _on_perm_resolved(self, event: PermissionResolved) -> None:
        if event.agent_id != self._agent_id:
            return
        from patchfeld.widgets.permission_request_bar import PermissionRequestBar
        for bar in list(self.query(PermissionRequestBar)):
            if bar.request_id == event.request_id:
                bar.remove()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return
        bus = self._bus or getattr(self.app, "event_bus", None)
        if bus is not None:
            bus.publish(DirectMessageToAgent(agent_id=self._agent_id, text=text))
        event.input.value = ""

    def rendered_text(self) -> str:
        return self.query_one(RichTranscript).rendered_text()

    @classmethod
    def default_border_title(cls, props: dict) -> str:
        agent_id = props.get("agent_id")
        if agent_id:
            return f"Agent: {agent_id}"
        return "Agent"
```

- [ ] **Step 5: Run, verify PASS**

Run: `pytest tests/test_widget_permission_request_bar.py tests/test_agent_transcript_input.py tests/test_agent_transcript_widget.py -v`
Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add patchfeld/widgets/permission_request_bar.py patchfeld/widgets/agent_transcript.py tests/test_widget_permission_request_bar.py
git commit -m "feat(transcript): mount inline PermissionRequestBar for pending requests"
```

---

### Task 14: App-level subscription pushes the modal on first `PermissionRequested`

The modal needs to be on the screen stack to subscribe and render. Approach: the app subscribes to `PermissionRequested` only when grants are wired, and on the *first* request (no modal up) pushes the modal once. Subsequent requests are absorbed by the modal's own subscription.

**Files:**
- Modify: `patchfeld/app.py` (`__init__` flag, `on_mount` subscribe, new handler)
- Test: `tests/test_app_smoke_permission_modal.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_app_smoke_permission_modal.py`:

```python
@pytest.mark.asyncio
async def test_permission_request_pushes_modal_when_grants_present(tmp_path: Path):
    from patchfeld.app import PatchfeldApp
    from patchfeld.events import PermissionRequested
    from patchfeld.widgets.permission_modal import PermissionModal

    app = PatchfeldApp(cwd=tmp_path, global_dir=tmp_path / "cfg")
    async with app.run_test() as pilot:
        await pilot.pause()
        app.event_bus.publish(PermissionRequested(
            agent_id="a1", agent_name="r", request_id="r1",
            tool_name="Read", tool_input={"path": "x"},
        ))
        await pilot.pause()
        assert isinstance(app.screen, PermissionModal)


@pytest.mark.asyncio
async def test_permission_request_does_nothing_when_bypass(tmp_path: Path):
    from patchfeld.app import PatchfeldApp
    from patchfeld.events import PermissionRequested
    from patchfeld.widgets.permission_modal import PermissionModal

    app = PatchfeldApp(
        cwd=tmp_path, global_dir=tmp_path / "cfg",
        bypass_permissions=True,
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        app.event_bus.publish(PermissionRequested(
            agent_id="a1", agent_name="r", request_id="r1",
            tool_name="Read", tool_input={},
        ))
        await pilot.pause()
        assert not isinstance(app.screen, PermissionModal)
```

- [ ] **Step 2: Run, verify FAIL**

Expected: first test fails (modal not pushed).

- [ ] **Step 3: Implement**

In `patchfeld/app.py`:

(a) Add import: `from patchfeld.events import PermissionRequested`.

(b) In `__init__`, add `self._permission_modal_open = False`.

(c) In `on_mount`, after the existing subscriptions:

```python
        if self._permission_grants is not None:
            self.event_bus.subscribe(
                PermissionRequested, self._on_permission_requested,
            )
```

(d) Add the handler method:

```python
    def _on_permission_requested(self, event: PermissionRequested) -> None:
        from patchfeld.widgets.permission_modal import PermissionModal
        if self._permission_modal_open:
            return  # the modal's own subscription will queue this
        self._permission_modal_open = True

        def _on_dismissed(_: object) -> None:
            self._permission_modal_open = False

        # Lookup is unified across orchestrator + child agents.
        def _inbox_lookup(agent_id: str):
            if agent_id == "orchestrator":
                return self.orchestrator.get_permission_inbox()
            return self.manager.get_permission_inbox(agent_id)

        self.push_screen(
            PermissionModal(
                inbox_lookup=_inbox_lookup,
                grants=self._permission_grants,
            ),
            _on_dismissed,
        )
```

- [ ] **Step 4: Run, verify PASS**

Run: `pytest tests/test_app_smoke_permission_modal.py -v`

- [ ] **Step 5: Commit**

```bash
git add patchfeld/app.py tests/test_app_smoke_permission_modal.py
git commit -m "feat(app): push PermissionModal on first PermissionRequested"
```

---

### Task 15: End-to-end smoke — child agent allow-once unblocks callback

**Files:**
- Append: `tests/test_app_smoke_permission_modal.py`

- [ ] **Step 1: Write the test**

```python
@pytest.mark.asyncio
async def test_e2e_child_allow_once_unblocks_callback(tmp_path: Path):
    import asyncio
    from claude_agent_sdk import (
        AssistantMessage, PermissionResultAllow, ResultMessage,
        TextBlock, ToolPermissionContext,
    )
    from patchfeld.app import PatchfeldApp
    from patchfeld.agents.fake_sdk_adapter import FakeSDKAdapter

    app = PatchfeldApp(cwd=tmp_path, global_dir=tmp_path / "cfg")
    async with app.run_test() as pilot:
        await pilot.pause()
        app.manager._adapter_factory = lambda: FakeSDKAdapter(scripts=[[
            AssistantMessage(content=[TextBlock(text="x")], model="m"),
            ResultMessage(
                subtype="success", duration_ms=1, duration_api_ms=1,
                is_error=False, num_turns=1, session_id="s",
                total_cost_usd=0.0, usage={"input_tokens": 1, "output_tokens": 1},
                result="x",
            ),
        ]])
        aid = await app.manager.spawn(name="researcher", prompt="hi")
        await app.manager.wait_idle(aid)

        info = next(i for i in app.manager.list_infos() if i.id == aid)
        callback = app.manager._build_options(info).can_use_tool
        ctx = ToolPermissionContext(tool_use_id="t1")

        cb_task = asyncio.create_task(callback("Read", {"path": "x"}, ctx))
        for _ in range(20):
            await pilot.pause()
            from patchfeld.widgets.permission_modal import PermissionModal
            if isinstance(app.screen, PermissionModal):
                break
        await pilot.click("#allow-once")
        await pilot.pause()
        result = await asyncio.wait_for(cb_task, timeout=2.0)
        assert isinstance(result, PermissionResultAllow)
```

- [ ] **Step 2: Run, verify PASS**

Run: `pytest tests/test_app_smoke_permission_modal.py::test_e2e_child_allow_once_unblocks_callback -v`
Expected: pass.

- [ ] **Step 3: Commit**

```bash
git add tests/test_app_smoke_permission_modal.py
git commit -m "test(app): e2e child allow-once unblocks can_use_tool"
```

---

### Task 16: End-to-end smoke — orchestrator allow-once

**Files:**
- Append: `tests/test_app_smoke_permission_modal.py`

- [ ] **Step 1: Write the test**

```python
@pytest.mark.asyncio
async def test_e2e_orchestrator_allow_once_unblocks_callback(tmp_path: Path):
    import asyncio
    from claude_agent_sdk import PermissionResultAllow, ToolPermissionContext
    from patchfeld.app import PatchfeldApp
    from patchfeld.widgets.permission_modal import PermissionModal

    app = PatchfeldApp(cwd=tmp_path, global_dir=tmp_path / "cfg")
    async with app.run_test() as pilot:
        await pilot.pause()
        callback = app.orchestrator._can_use_tool_callback
        assert callback is not None  # grants are wired
        ctx = ToolPermissionContext(tool_use_id="t1")

        cb_task = asyncio.create_task(
            callback("mcp__patchfeld_orchestrator__list_widgets", {}, ctx)
        )
        for _ in range(20):
            await pilot.pause()
            if isinstance(app.screen, PermissionModal):
                break
        # The modal should advertise "the orchestrator" on the always-allow button.
        button = app.screen.query_one("#allow-always")
        assert "orchestrator" in str(button.label).lower()
        await pilot.click("#allow-once")
        await pilot.pause()
        result = await asyncio.wait_for(cb_task, timeout=2.0)
        assert isinstance(result, PermissionResultAllow)
```

- [ ] **Step 2: Run, verify PASS**

Run: `pytest tests/test_app_smoke_permission_modal.py::test_e2e_orchestrator_allow_once_unblocks_callback -v`
Expected: pass.

- [ ] **Step 3: Commit**

```bash
git add tests/test_app_smoke_permission_modal.py
git commit -m "test(app): e2e orchestrator allow-once unblocks can_use_tool"
```

---

### Task 17: `--bypass-permissions` is observably a no-op vs pre-feature behavior

This is a regression check: with the flag passed, the build_options output and event subscriptions must match the pre-feature codebase.

**Files:**
- Append: `tests/test_app_smoke_permission_modal.py`

- [ ] **Step 1: Write the test**

```python
@pytest.mark.asyncio
async def test_bypass_skips_modal_subscriptions(tmp_path: Path):
    from patchfeld.app import PatchfeldApp
    from patchfeld.events import PermissionRequested

    app = PatchfeldApp(
        cwd=tmp_path, global_dir=tmp_path / "cfg",
        bypass_permissions=True,
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        # Manager and orchestrator are bypass-mode.
        assert app.manager._grants is None
        assert app.orchestrator.permission_grants is None
        # No callback wired.
        assert app.orchestrator._can_use_tool_callback is None
        # No PermissionRequested handler on the bus.
        handlers = app.event_bus._subs.get(PermissionRequested, [])
        assert handlers == []
```

- [ ] **Step 2: Run, verify PASS**

Run: `pytest tests/test_app_smoke_permission_modal.py::test_bypass_skips_modal_subscriptions -v`

- [ ] **Step 3: Commit**

```bash
git add tests/test_app_smoke_permission_modal.py
git commit -m "test(app): --bypass-permissions wires nothing"
```

---

### Task 18: Refresh stale `permission_mode` comments

Both `AgentManager._build_options` and `OrchestratorSession._build_and_start_inner` carry "plan-3 work" comments that are now obsolete.

**Files:**
- Modify: `patchfeld/agents/manager.py` (existing comment block)
- Modify: `patchfeld/orchestrator/session.py` (existing comment block)

- [ ] **Step 1: Replace the manager comment**

In `patchfeld/agents/manager.py`, the comment that says "Bypass permissions for now: there's no Textual modal..." now reads:

```python
        # Permission posture: presence of self._grants is the gate.
        #   - None  → permission_mode="bypassPermissions" (preserves the
        #     original behavior; equivalent to launching with
        #     --bypass-permissions).
        #   - obj   → drop bypass, attach can_use_tool that consults the
        #     grants store first and falls back to the modal flow.
```

- [ ] **Step 2: Replace the orchestrator comment**

In `patchfeld/orchestrator/session.py`, the comment that says "The orchestrator is the user's trusted manager session — there's no UI in the TUI yet to render a permission prompt..." now reads:

```python
        # Permission posture mirrors AgentManager (see manager.py): when
        # the user launches without --bypass-permissions, self._grants is
        # set and we attach can_use_tool. The orchestrator routes through
        # the same PermissionModal as child agents — the user reviews each
        # tool call the orchestrator's Claude wants to make.
```

- [ ] **Step 3: Run full test suite**

Run: `pytest -x`
Expected: all green.

- [ ] **Step 4: Commit**

```bash
git add patchfeld/agents/manager.py patchfeld/orchestrator/session.py
git commit -m "docs: refresh permission_mode comments for new flag"
```

---

### Task 19: Final smoke + manual verification

- [ ] **Step 1: Run the full suite**

Run: `pytest -x -v`
Expected: all green.

- [ ] **Step 2: Manual smoke (record in PR description)**

1. `patchfeld` (no flag) → typing anything in the orchestrator chat that requires a tool call pops a `PermissionModal` with "agent: orchestrator".
2. Click **Always allow** for some safe tool → confirm `.patchfeld/permission_grants.json` contains the rule.
3. Restart `patchfeld` (no flag) → ask the orchestrator to use that same tool again → no modal.
4. Have the orchestrator spawn an agent named "researcher" — a NEW modal pops for the orchestrator's `spawn_agent` call. Allow once.
5. Researcher's first tool call pops a modal labeled "agent: researcher". Confirm the AgentTable shows "researcher" in **orange `awaiting_permission`** while the modal is open.
6. Open an `AgentTranscript` panel for "researcher" and let it run another tool call — confirm the inline `PermissionRequestBar` appears at the top of the panel and resolves the request when clicked.
7. `patchfeld --bypass-permissions` → no modals, no inline bars; behavior is bit-for-bit the pre-feature codebase.

- [ ] **Step 3: No further commits unless the run revealed bugs**

---

## Section 6 — Self-Review Checklist (run before declaring done)

- [ ] Spec coverage:
  - [x] Bypass mode preserved via `--bypass-permissions` (Tasks 8, 9, 10, 17).
  - [x] Modal mode shows agent + tool + args + buttons, **for orchestrator and children** (Tasks 12, 14, 15, 16).
  - [x] Per-agent transcript inline approval (Task 13).
  - [x] `awaiting_permission` AgentState + orange status cell (Tasks 1, 2).
  - [x] Persisted "always allow", with orchestrator-aware wording (Tasks 4, 12).
  - [x] Edge cases — multi simultaneous (Task 12 queue), kill mid-request (Tasks 7, 9 cancel_all), Esc = deny (Task 12), session-only grants (Task 4).
- [ ] No placeholders.
- [ ] Type consistency: `PermissionGrants.lookup` returns `Behavior | None` consistently.
- [ ] Each task ends with a passing test command and a commit.
- [ ] Affected files match Section 2.

---

## Section 7 — Affected Files Summary

**Created (12 files):**
- `patchfeld/agents/permission_inbox.py`
- `patchfeld/agents/permission_grants.py`
- `patchfeld/widgets/permission_modal.py`
- `patchfeld/widgets/permission_request_bar.py`
- `tests/test_permission_inbox.py`
- `tests/test_permission_grants.py`
- `tests/test_widget_permission_modal.py`
- `tests/test_widget_permission_request_bar.py`
- `tests/test_agent_manager_can_use_tool.py`
- `tests/test_orchestrator_session_can_use_tool.py`
- `tests/test_app_smoke_permission_modal.py`
- `tests/test_main_argparse.py`

**Modified (10 files):**
- `patchfeld/__main__.py`
- `patchfeld/app.py`
- `patchfeld/agents/state.py`
- `patchfeld/agents/session.py`
- `patchfeld/agents/manager.py`
- `patchfeld/orchestrator/session.py`
- `patchfeld/events.py`
- `patchfeld/widgets/agent_table.py`
- `patchfeld/widgets/agent_transcript.py`
- `tests/test_agent_state.py` · `tests/test_agent_table_widget.py` · `tests/test_agent_session.py` · `tests/test_agent_manager.py` (small additions)
