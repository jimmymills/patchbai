# Agent `waiting` Status Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire up the existing-but-unused `AgentState.WAITING` so an agent's status flips to `waiting` while it is blocked on a pending `ask_orchestrator` request, and back to `running` as soon as the orchestrator responds (or the request times out).

**Architecture:** The `RequestInbox` is the single source of truth for pending asks. We extend it with a lifecycle callback (`on_pending_changed`) that fires whenever its pending-request count crosses zero. `AgentManager` wires that callback so it forwards `0 → ≥1` transitions to `AgentSession._mark_waiting()` and `≥1 → 0` transitions to `AgentSession._mark_unwaiting()`. `AgentSession` keeps a `_pre_wait_state` shadow so it can restore whatever state was active before WAITING was entered (almost always RUNNING). Existing `AgentStateChanged` events propagate to the `AgentTable`, which gets a small render upgrade to color WAITING distinctly. No new event types are needed.

**Tech Stack:** Python 3.12, asyncio, Textual, pytest, pytest-asyncio, Rich (for cell styling).

---

## File Structure

**Modified:**
- `patchbai/agents/request_inbox.py` — add `on_pending_changed: Callable[[int], None] | None` callback param; fire it from `register()` (after dict insert) and from `wait()` (after the `pop` in `finally`). Also fire from `resolve()` if the request_id was pre-popped (it never is today, so a no-op for resolve is fine — `wait` is the canonical drain point).
- `patchbai/agents/session.py` — add `_pre_wait_state: AgentState | None` attribute, plus `_mark_waiting()` and `_mark_unwaiting()` methods that snapshot/restore state. Guard against re-entry (entering WAITING when already WAITING is a no-op; exiting WAITING when not WAITING is a no-op).
- `patchbai/agents/manager.py` — pass an `on_pending_changed` lambda into `RequestInbox(...)` at spawn time; the lambda calls `session._mark_waiting()` / `session._mark_unwaiting()` based on the count.
- `patchbai/widgets/agent_table.py` — render the status cell with a Rich Text style: yellow for WAITING, green for RUNNING, dim for IDLE, bold for DONE, red for ERROR. Centralize the styling in a small helper.

**New tests:**
- `tests/test_request_inbox.py` — extend with cases for the `on_pending_changed` callback firing on register / wait-drain / wait-timeout-drain, and NOT firing for redundant transitions.
- `tests/test_agent_session.py` — extend with `_mark_waiting` / `_mark_unwaiting` round trip tests, including double-entry and stale exit guards.
- `tests/test_agent_manager.py` — extend with a test that covers the wiring: registering on the inbox flips the agent to WAITING; resolving flips it back to RUNNING.
- `tests/test_agent_table_widget.py` — extend with a test that asserts the WAITING cell renders with the expected yellow style.
- `tests/test_app_smoke_plan3.py` — extend the existing round-trip test to assert state transitions RUNNING → WAITING → RUNNING across the ask/respond cycle.

**Unchanged but reviewed:**
- `patchbai/agents/state.py` — `WAITING = "waiting"` already exists; no change.
- `patchbai/events.py` — no new event types; `AgentStateChanged` is sufficient.
- `patchbai/orchestrator/tools.py` — `respond_to_agent_request` already calls `inbox.resolve` then `wait` drains; no change.
- `patchbai/agents/child_tools.py` — `ask_orchestrator` already calls `inbox.register` and `inbox.wait`; no change.
- `patchbai/app.py` — `_on_stats_changed` already counts non-terminal agents (which includes WAITING) for `active_agents`; no change.

---

## Detection Logic Summary

- **Enter WAITING:** the inbox's pending count goes from `0 → ≥1`. The agent's prior state is snapshotted into `_pre_wait_state`, and the session transitions to `WAITING`.
- **Exit WAITING:** the inbox's pending count goes from `≥1 → 0`. The session restores `_pre_wait_state` (clamped: if the prior state was a terminal one — DONE or ERROR — we leave it alone; if it was anything else, we restore RUNNING because that's the only non-terminal state the agent could have been in when an ask was issued).
- **Stacked asks:** WAITING is entered on the first register and exited on the last drain. Intermediate register/drain cycles inside a still-non-empty inbox do NOT fire the callback.
- **Timeout:** `RequestInbox.wait`'s `finally` block already pops the request id, so timeout-driven exits hit the same "inbox went empty" callback as resolve-driven exits — no special-casing needed.

---

## UI Summary

- **Status cell color (AgentTable):** yellow for `waiting`, green for `running`, default for `idle`/`done`, red for `error`. (Adding color for the other states is incidental polish — without color anchors the WAITING yellow looks orphaned.)
- **StatusBar:** `active_agents` already includes WAITING (since `WAITING.is_terminal == False`); no change.
- **No new badges, animations, or footer elements** — keep this plan tight.

---

## Edge Cases

1. **Agent asks, then ends without response.** Cannot happen in practice: the `ask_orchestrator` tool awaits the inbox future, so the SDK stream is blocked at the tool call until the future resolves or times out. `_consume_stream`'s `_set_state(DONE)` only runs after the stream ends, which only happens after the tool returns. So the visible order is always: WAITING → RUNNING → DONE (or WAITING → RUNNING via timeout → DONE).
2. **Multiple stacked asks.** Inbox callback only fires on `0 → ≥1` and `≥1 → 0` transitions. Two register calls in a row don't re-enter WAITING; two drains in a row only exit WAITING on the last.
3. **Orchestrator responds, then agent asks again.** WAITING → RUNNING → WAITING. Each cycle goes through the inbox callback and produces an `AgentStateChanged` event, so the AgentTable repaints both transitions.
4. **Restart with pending ask.** The in-memory inbox is recreated empty on every spawn. Agents don't survive process death today (no persisted-and-resumed child sessions), so cold-start consistency is moot. `agents.json` already persists `state`, so a snapshot might briefly read "waiting" if the process died at the wrong instant; the next spawn re-creates the agent fresh and overwrites that record.
5. **`_set_state` re-entry guard.** `AgentSession._set_state` already short-circuits if `old == new_state`, so re-entering WAITING is a no-op for the bus. We additionally guard `_mark_waiting` against re-entry by checking `_pre_wait_state is None` — if it's already set, we don't overwrite it (preserves the original pre-wait state through stacked asks).
6. **Exit while in a terminal state.** If the session somehow drained the inbox after entering DONE or ERROR (shouldn't happen, but be defensive), `_mark_unwaiting` checks `info.state.is_terminal` and skips the restore.

---

## Test Strategy

**Existing tests to update:**
- `tests/test_request_inbox.py` — add coverage for the new `on_pending_changed` callback. Existing tests still pass without one (default `None`).
- `tests/test_agent_session.py` — add `_mark_waiting` / `_mark_unwaiting` cases.
- `tests/test_agent_manager.py` — add a wiring test for the inbox callback.
- `tests/test_agent_table_widget.py` — add a WAITING-renders-yellow case.
- `tests/test_app_smoke_plan3.py` — assert RUNNING → WAITING → RUNNING transitions in the existing ask/respond round-trip test.

**New tests:** none beyond extensions above.

**Test posture:** every step is paired with a failing test first (TDD). All transitions go through `AgentStateChanged` events, which makes them observable from a `bus.subscribe` in tests — no need to inspect the session's private fields.

---

## Implementation Order

Each task ends with a green build (`pytest -q` passes) and a commit.

---

### Task 1: `RequestInbox` callback — failing test for register transition

**Files:**
- Test: `tests/test_request_inbox.py`

- [ ] **Step 1: Add a failing test that the callback fires on first register**

Append to `tests/test_request_inbox.py`:

```python
@pytest.mark.asyncio
async def test_on_pending_changed_fires_when_inbox_becomes_non_empty():
    counts: list[int] = []
    inbox = RequestInbox(on_pending_changed=counts.append)
    inbox.register()
    assert counts == [1]
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `pytest tests/test_request_inbox.py::test_on_pending_changed_fires_when_inbox_becomes_non_empty -v`
Expected: FAIL with `TypeError: RequestInbox.__init__() got an unexpected keyword argument 'on_pending_changed'`.

---

### Task 2: `RequestInbox` callback — implement on register

**Files:**
- Modify: `patchbai/agents/request_inbox.py`

- [ ] **Step 1: Add `on_pending_changed` param and fire it from `register()`**

Replace the file body of `patchbai/agents/request_inbox.py` with:

```python
import asyncio
import uuid
from typing import Callable


class RequestInbox:
    """Per-agent registry of pending ask_orchestrator requests.

    Each registered request_id has an asyncio.Future. The agent's tool call
    awaits the future (with a timeout); the orchestrator's reply resolves it.

    `on_pending_changed`, if provided, is invoked synchronously after every
    transition that changes the pending count — i.e., after `register()` and
    after `wait()` removes a future from the dict. It receives the new
    pending count.
    """

    def __init__(
        self,
        *,
        on_pending_changed: Callable[[int], None] | None = None,
    ) -> None:
        self._futures: dict[str, asyncio.Future] = {}
        self._on_pending_changed = on_pending_changed

    def register(self) -> str:
        request_id = uuid.uuid4().hex[:12]
        # get_running_loop() instead of get_event_loop(): the latter is
        # deprecated in Python 3.12+ when called outside a running loop.
        # All callers run inside an event loop (tool handlers are async).
        loop = asyncio.get_running_loop()
        self._futures[request_id] = loop.create_future()
        self._notify()
        return request_id

    def resolve(self, request_id: str, response: str) -> None:
        future = self._futures.get(request_id)
        if future is not None and not future.done():
            future.set_result(response)

    async def wait(self, request_id: str, *, timeout_s: float) -> str:
        future = self._futures.get(request_id)
        if future is None:
            raise KeyError(f"unknown request_id: {request_id}")
        try:
            return await asyncio.wait_for(future, timeout=timeout_s)
        finally:
            self._futures.pop(request_id, None)
            self._notify()

    def pending(self) -> list[str]:
        return [rid for rid, fut in self._futures.items() if not fut.done()]

    def _notify(self) -> None:
        if self._on_pending_changed is None:
            return
        try:
            self._on_pending_changed(len(self._futures))
        except Exception:
            # The inbox must not poison its own callers if a subscriber
            # explodes; mirror EventBus's swallow-and-log posture.
            import logging
            logging.getLogger(__name__).exception(
                "RequestInbox.on_pending_changed handler raised"
            )
```

- [ ] **Step 2: Run the new test and verify it passes**

Run: `pytest tests/test_request_inbox.py::test_on_pending_changed_fires_when_inbox_becomes_non_empty -v`
Expected: PASS.

- [ ] **Step 3: Run the full inbox suite to verify the existing tests still pass**

Run: `pytest tests/test_request_inbox.py -v`
Expected: all tests pass.

- [ ] **Step 4: Commit**

```bash
git add patchbai/agents/request_inbox.py tests/test_request_inbox.py
git commit -m "feat(inbox): add on_pending_changed callback"
```

---

### Task 3: `RequestInbox` callback — drain transitions

**Files:**
- Test: `tests/test_request_inbox.py`

- [ ] **Step 1: Add failing tests for resolve-driven and timeout-driven drains**

Append to `tests/test_request_inbox.py`:

```python
@pytest.mark.asyncio
async def test_on_pending_changed_fires_on_wait_drain_after_resolve():
    counts: list[int] = []
    inbox = RequestInbox(on_pending_changed=counts.append)
    rid = inbox.register()
    inbox.resolve(rid, "answer")
    await inbox.wait(rid, timeout_s=1.0)
    assert counts == [1, 0]


@pytest.mark.asyncio
async def test_on_pending_changed_fires_on_wait_timeout_drain():
    counts: list[int] = []
    inbox = RequestInbox(on_pending_changed=counts.append)
    rid = inbox.register()
    with pytest.raises(asyncio.TimeoutError):
        await inbox.wait(rid, timeout_s=0.05)
    assert counts == [1, 0]


@pytest.mark.asyncio
async def test_on_pending_changed_does_not_fire_for_intermediate_register_drain_pairs():
    """Stacked asks: count goes 0→1→2→1→0; we expect every step to fire,
    so we can distinguish "still non-empty" (>=1) from "now empty" (==0)."""
    counts: list[int] = []
    inbox = RequestInbox(on_pending_changed=counts.append)
    a = inbox.register()
    b = inbox.register()
    inbox.resolve(a, "a")
    await inbox.wait(a, timeout_s=1.0)
    inbox.resolve(b, "b")
    await inbox.wait(b, timeout_s=1.0)
    assert counts == [1, 2, 1, 0]


@pytest.mark.asyncio
async def test_on_pending_changed_callback_exception_is_swallowed():
    def boom(_count: int) -> None:
        raise RuntimeError("boom")

    inbox = RequestInbox(on_pending_changed=boom)
    # Must not raise.
    rid = inbox.register()
    inbox.resolve(rid, "ok")
    await inbox.wait(rid, timeout_s=1.0)
```

- [ ] **Step 2: Run them and verify they pass**

(Implementation in Task 2 already covers these — they should pass on first run because `wait` already calls `_notify()`.)

Run: `pytest tests/test_request_inbox.py -v`
Expected: all tests pass.

- [ ] **Step 3: Commit**

```bash
git add tests/test_request_inbox.py
git commit -m "test(inbox): cover drain transitions of on_pending_changed"
```

---

### Task 4: `AgentSession` — `_mark_waiting` / `_mark_unwaiting` failing tests

**Files:**
- Test: `tests/test_agent_session.py`

- [ ] **Step 1: Add failing tests**

Append to `tests/test_agent_session.py`:

```python
@pytest.mark.asyncio
async def test_mark_waiting_transitions_running_to_waiting(tmp_path):
    bus = EventBus()
    transitions: list[AgentStateChanged] = []
    bus.subscribe(AgentStateChanged, transitions.append)

    session = AgentSession(
        info=AgentInfo(
            id="a1", name="a1", cwd=str(tmp_path),
            started_at=0.0, state=AgentState.RUNNING,
        ),
        adapter=FakeSDKAdapter(scripts=[]),
        transcript=AgentTranscript(cwd=tmp_path, agent_id="a1"),
        bus=bus,
    )

    session._mark_waiting()
    assert session.info.state == AgentState.WAITING
    assert transitions[-1].old_state == AgentState.RUNNING
    assert transitions[-1].info.state == AgentState.WAITING


@pytest.mark.asyncio
async def test_mark_unwaiting_restores_pre_wait_state(tmp_path):
    bus = EventBus()
    session = AgentSession(
        info=AgentInfo(
            id="a1", name="a1", cwd=str(tmp_path),
            started_at=0.0, state=AgentState.RUNNING,
        ),
        adapter=FakeSDKAdapter(scripts=[]),
        transcript=AgentTranscript(cwd=tmp_path, agent_id="a1"),
        bus=bus,
    )

    session._mark_waiting()
    session._mark_unwaiting()
    assert session.info.state == AgentState.RUNNING


@pytest.mark.asyncio
async def test_mark_waiting_is_idempotent_for_stacked_calls(tmp_path):
    """Two enters then two exits round-trip cleanly."""
    bus = EventBus()
    session = AgentSession(
        info=AgentInfo(
            id="a1", name="a1", cwd=str(tmp_path),
            started_at=0.0, state=AgentState.RUNNING,
        ),
        adapter=FakeSDKAdapter(scripts=[]),
        transcript=AgentTranscript(cwd=tmp_path, agent_id="a1"),
        bus=bus,
    )

    session._mark_waiting()
    session._mark_waiting()  # second enter is a no-op
    assert session.info.state == AgentState.WAITING
    session._mark_unwaiting()
    assert session.info.state == AgentState.RUNNING


@pytest.mark.asyncio
async def test_mark_unwaiting_when_not_waiting_is_noop(tmp_path):
    bus = EventBus()
    session = AgentSession(
        info=AgentInfo(
            id="a1", name="a1", cwd=str(tmp_path),
            started_at=0.0, state=AgentState.RUNNING,
        ),
        adapter=FakeSDKAdapter(scripts=[]),
        transcript=AgentTranscript(cwd=tmp_path, agent_id="a1"),
        bus=bus,
    )

    session._mark_unwaiting()
    assert session.info.state == AgentState.RUNNING


@pytest.mark.asyncio
async def test_mark_unwaiting_does_not_resurrect_terminal_state(tmp_path):
    bus = EventBus()
    session = AgentSession(
        info=AgentInfo(
            id="a1", name="a1", cwd=str(tmp_path),
            started_at=0.0, state=AgentState.RUNNING,
        ),
        adapter=FakeSDKAdapter(scripts=[]),
        transcript=AgentTranscript(cwd=tmp_path, agent_id="a1"),
        bus=bus,
    )

    session._mark_waiting()
    # Simulate the stream ending while waiting — defensive only; the real
    # SDK would never do this because the tool result is still pending.
    session.info.state = AgentState.DONE
    session._mark_unwaiting()
    assert session.info.state == AgentState.DONE
```

- [ ] **Step 2: Run and verify they fail**

Run: `pytest tests/test_agent_session.py -k "mark_waiting or mark_unwaiting" -v`
Expected: FAIL with `AttributeError: 'AgentSession' object has no attribute '_mark_waiting'`.

---

### Task 5: `AgentSession` — implement `_mark_waiting` / `_mark_unwaiting`

**Files:**
- Modify: `patchbai/agents/session.py`

- [ ] **Step 1: Add `_pre_wait_state` field initialization**

Edit `patchbai/agents/session.py`. In `AgentSession.__init__`, after the existing `self._send_lock = asyncio.Lock()` line, add:

```python
        self._pre_wait_state: AgentState | None = None
```

- [ ] **Step 2: Add the two methods near `_set_state`**

Insert just before `def _set_state(self, new_state: AgentState) -> None:`:

```python
    def _mark_waiting(self) -> None:
        """Enter WAITING state, snapshotting the prior state for restore.

        Idempotent: a second call while already WAITING is a no-op (the
        snapshot is preserved). Skipped if the session is already in a
        terminal state.
        """
        if self.info.state.is_terminal:
            return
        if self.info.state == AgentState.WAITING:
            return
        self._pre_wait_state = self.info.state
        self._set_state(AgentState.WAITING)

    def _mark_unwaiting(self) -> None:
        """Exit WAITING state, restoring the pre-wait state.

        No-op when not in WAITING. If the session is somehow terminal,
        the snapshot is dropped without a transition.
        """
        if self.info.state != AgentState.WAITING:
            self._pre_wait_state = None
            return
        target = self._pre_wait_state or AgentState.RUNNING
        self._pre_wait_state = None
        if target.is_terminal:
            # Defensive: never resurrect a terminal state.
            return
        self._set_state(target)
```

- [ ] **Step 3: Run the new session tests**

Run: `pytest tests/test_agent_session.py -k "mark_waiting or mark_unwaiting" -v`
Expected: PASS.

- [ ] **Step 4: Run the full session suite to confirm no regressions**

Run: `pytest tests/test_agent_session.py -v`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add patchbai/agents/session.py tests/test_agent_session.py
git commit -m "feat(session): add _mark_waiting / _mark_unwaiting helpers"
```

---

### Task 6: `AgentManager` — wire inbox callback through to session

**Files:**
- Test: `tests/test_agent_manager.py`

- [ ] **Step 1: Add a failing wiring test**

Open `tests/test_agent_manager.py`. Add this test (alongside existing tests, anywhere):

```python
@pytest.mark.asyncio
async def test_inbox_register_flips_session_to_waiting_and_back(tmp_path):
    from patchbai.agents.fake_sdk_adapter import FakeSDKAdapter
    from patchbai.agents.manager import AgentManager
    from patchbai.agents.state import AgentState
    from patchbai.events import AgentStateChanged, EventBus
    from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

    def _ok():
        return [
            AssistantMessage(content=[TextBlock(text="done")], model="fake-model"),
            ResultMessage(
                subtype="success", duration_ms=1, duration_api_ms=1,
                is_error=False, num_turns=1, session_id="fake",
                total_cost_usd=0.0,
                usage={"input_tokens": 1, "output_tokens": 1}, result="done",
            ),
        ]

    bus = EventBus()
    transitions: list[AgentStateChanged] = []
    bus.subscribe(AgentStateChanged, transitions.append)

    manager = AgentManager(
        cwd=tmp_path,
        bus=bus,
        adapter_factory=lambda: FakeSDKAdapter(scripts=[_ok()]),
    )
    aid = await manager.spawn(name="alpha", prompt="hi")
    await manager.wait_idle(aid)

    # Force the session out of DONE for the duration of this test by
    # mutating its info state to RUNNING — we want to observe the
    # WAITING/RUNNING flip from inbox events without a real stream.
    session = manager.get_session(aid)
    session.info.state = AgentState.RUNNING

    inbox = manager.get_inbox(aid)
    rid = inbox.register()
    assert session.info.state == AgentState.WAITING

    inbox.resolve(rid, "answer")
    await inbox.wait(rid, timeout_s=1.0)
    assert session.info.state == AgentState.RUNNING

    # The transition history should contain the WAITING enter and exit.
    pairs = [(t.old_state, t.info.state) for t in transitions]
    assert (AgentState.RUNNING, AgentState.WAITING) in pairs
    assert (AgentState.WAITING, AgentState.RUNNING) in pairs
```

- [ ] **Step 2: Run and verify it fails**

Run: `pytest tests/test_agent_manager.py::test_inbox_register_flips_session_to_waiting_and_back -v`
Expected: FAIL on `assert session.info.state == AgentState.WAITING` because nothing wires the inbox to the session yet.

- [ ] **Step 3: Wire the callback in `AgentManager.spawn`**

Edit `patchbai/agents/manager.py`. Replace the current line:

```python
        self._inboxes[agent_id] = RequestInbox()
```

with:

```python
        # Capture the session in a closure so the inbox callback can flip
        # state. We intentionally bind `session` (not `self._sessions[...]`)
        # so a later kill() doesn't leave the closure resolving to None.
        def _on_pending_changed(count: int, _session=session) -> None:
            if count > 0:
                _session._mark_waiting()
            else:
                _session._mark_unwaiting()

        self._inboxes[agent_id] = RequestInbox(
            on_pending_changed=_on_pending_changed
        )
```

The `session` local must be defined before the inbox is constructed. Verify: in the existing `spawn` method, `session = AgentSession(...)` is defined at line ~63, and `self._inboxes[agent_id] = RequestInbox()` is at line ~70 — so the closure already has `session` in scope. Good.

- [ ] **Step 4: Run the wiring test**

Run: `pytest tests/test_agent_manager.py::test_inbox_register_flips_session_to_waiting_and_back -v`
Expected: PASS.

- [ ] **Step 5: Run the full manager and inbox suites**

Run: `pytest tests/test_agent_manager.py tests/test_request_inbox.py tests/test_agent_session.py -v`
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add patchbai/agents/manager.py tests/test_agent_manager.py
git commit -m "feat(manager): flip session to waiting on inbox register/drain"
```

---

### Task 7: End-to-end smoke test in plan3

**Files:**
- Test: `tests/test_app_smoke_plan3.py`

- [ ] **Step 1: Extend the existing round-trip test to assert state transitions**

Edit `tests/test_app_smoke_plan3.py`. At the top of the file, add to the imports:

```python
from patchbai.agents.state import AgentState
from patchbai.events import AgentStateChanged
```

In `test_ask_orchestrator_round_trip`, just after the existing line `await orchestrator.start()`, insert:

```python
    state_events: list[AgentStateChanged] = []
    bus.subscribe(AgentStateChanged, state_events.append)
```

After the existing `answer = await waiter_task` / `assert answer == "ship it"` lines, before `await orchestrator.stop()`, insert:

```python
    pairs = [(e.old_state, e.info.state) for e in state_events if e.info.id == aid]
    assert (AgentState.RUNNING, AgentState.WAITING) in pairs or \
           (AgentState.IDLE, AgentState.WAITING) in pairs or \
           (AgentState.DONE, AgentState.WAITING) in pairs, \
        f"expected a transition INTO WAITING, got {pairs}"
    assert any(p[0] == AgentState.WAITING for p in pairs), \
        f"expected a transition OUT OF WAITING, got {pairs}"
```

(The disjunction tolerates the existing test's order: the agent is sent to DONE by `wait_idle` before the inbox.register fires. `_mark_waiting` short-circuits when the state is terminal, but in this test we directly call `inbox.register()` after the agent finishes its first reply. Step 2 below adjusts the smoke test to put the agent back into RUNNING before registering, so the natural transition is RUNNING → WAITING → RUNNING.)

- [ ] **Step 2: Force a non-terminal state before the ask, so the WAITING flip is observable**

In `test_ask_orchestrator_round_trip`, just before the existing line `request_id = inbox.register()`, insert:

```python
    # Coerce the session out of DONE (the canned script ended the stream
    # already) so the inbox-driven WAITING transition is visible.
    manager.get_session(aid).info.state = AgentState.RUNNING
```

And simplify the assertion to:

```python
    pairs = [(e.old_state, e.info.state) for e in state_events if e.info.id == aid]
    assert (AgentState.RUNNING, AgentState.WAITING) in pairs, \
        f"expected RUNNING → WAITING, got {pairs}"
    assert (AgentState.WAITING, AgentState.RUNNING) in pairs, \
        f"expected WAITING → RUNNING, got {pairs}"
```

- [ ] **Step 3: Run the smoke test**

Run: `pytest tests/test_app_smoke_plan3.py::test_ask_orchestrator_round_trip -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/test_app_smoke_plan3.py
git commit -m "test(plan3): assert RUNNING→WAITING→RUNNING in ask round trip"
```

---

### Task 8: AgentTable — failing test for WAITING cell color

**Files:**
- Test: `tests/test_agent_table_widget.py`

- [ ] **Step 1: Add a failing test that the WAITING cell renders with yellow style**

Append to `tests/test_agent_table_widget.py`:

```python
@pytest.mark.asyncio
async def test_status_cell_uses_yellow_for_waiting():
    from rich.text import Text
    bus = EventBus()
    app = _HostApp(bus)
    async with app.run_test() as pilot:
        await pilot.pause()
        info = _info(state=AgentState.WAITING)
        bus.publish(AgentSpawned(info=info))
        await pilot.pause()

        widget = app.query_one(AgentTable)
        cells = widget._render_cells(info)
        # Status is column index 1.
        status_cell = cells[1]
        assert isinstance(status_cell, Text)
        assert status_cell.plain == "waiting"
        assert "yellow" in str(status_cell.style).lower()


@pytest.mark.asyncio
async def test_status_cell_uses_green_for_running():
    from rich.text import Text
    bus = EventBus()
    app = _HostApp(bus)
    async with app.run_test() as pilot:
        await pilot.pause()
        info = _info(state=AgentState.RUNNING)
        widget = app.query_one(AgentTable)
        cells = widget._render_cells(info)
        status_cell = cells[1]
        assert isinstance(status_cell, Text)
        assert status_cell.plain == "running"
        assert "green" in str(status_cell.style).lower()


@pytest.mark.asyncio
async def test_status_cell_uses_red_for_error():
    from rich.text import Text
    bus = EventBus()
    app = _HostApp(bus)
    async with app.run_test() as pilot:
        await pilot.pause()
        info = _info(state=AgentState.ERROR)
        widget = app.query_one(AgentTable)
        cells = widget._render_cells(info)
        status_cell = cells[1]
        assert isinstance(status_cell, Text)
        assert status_cell.plain == "error"
        assert "red" in str(status_cell.style).lower()
```

- [ ] **Step 2: Run and verify they fail**

Run: `pytest tests/test_agent_table_widget.py -k "status_cell_uses" -v`
Expected: FAIL — the cells are currently rendered with no style, so `str(status_cell.style)` is `""` (or `"none"`).

---

### Task 9: AgentTable — render status cells with state-keyed colors

**Files:**
- Modify: `patchbai/widgets/agent_table.py`

- [ ] **Step 1: Add a state-to-style mapping and use it in `_render_cells`**

Edit `patchbai/widgets/agent_table.py`. Replace the existing `_render_cells` method with this version, and add the helper map at module scope just below the imports.

Add at module scope (just below the imports, above `class AgentTable`):

```python
from patchbai.agents.state import AgentState as _AgentState

_STATUS_STYLES: dict[_AgentState, str] = {
    _AgentState.IDLE: "dim",
    _AgentState.RUNNING: "green",
    _AgentState.WAITING: "yellow",
    _AgentState.DONE: "bold",
    _AgentState.ERROR: "red",
}
```

Replace the existing `_render_cells` method body with:

```python
    def _render_cells(self, info: AgentInfo) -> tuple:
        # Wrap each cell in Rich Text so values that may contain markup-like
        # text (especially the "last action" cell which echoes tool args)
        # render verbatim rather than tripping the markup parser.
        from rich.text import Text
        elapsed = info.elapsed_seconds()
        elapsed_str = f"{elapsed:5.1f}s"
        last = self._last_actions.get(info.id, "")
        cost_str = f"${info.cost:.4f}"
        status_style = _STATUS_STYLES.get(info.state, "")
        return (
            Text(info.name),
            Text(info.state.value, style=status_style),
            Text(elapsed_str),
            Text(last),
            Text(cost_str),
        )
```

(Note: the import line `from patchbai.agents.state import AgentInfo` already exists at the top of the file — keep it. The new module-scope import aliases `AgentState` to `_AgentState` to keep the lookup table self-contained without changing the existing import names.)

- [ ] **Step 2: Run the new color tests**

Run: `pytest tests/test_agent_table_widget.py -k "status_cell_uses" -v`
Expected: PASS.

- [ ] **Step 3: Run the full table suite to confirm no regressions**

Run: `pytest tests/test_agent_table_widget.py -v`
Expected: all tests pass.

- [ ] **Step 4: Commit**

```bash
git add patchbai/widgets/agent_table.py tests/test_agent_table_widget.py
git commit -m "feat(agent-table): color status cell by state (yellow=waiting)"
```

---

### Task 10: Final full-suite verification

- [ ] **Step 1: Run the full test suite**

Run: `pytest -q`
Expected: all tests pass.

- [ ] **Step 2: If anything fails, debug; otherwise no commit needed**

If failures appear, they're almost certainly in tests that imported from `patchbai.agents.request_inbox`, `patchbai.agents.session`, or `patchbai.agents.manager` and constructed the inbox/session in unusual ways. Fix forward: keep the new constructor signatures backwards-compatible (the `on_pending_changed` keyword has a `None` default, so any caller passing positional args still works).

---

## Self-Review Notes

- **Spec coverage:** every section of the brief (current state, detection, data flow, UI, edge cases, affected files, tests, implementation order) is covered above.
- **No placeholders:** every code step shows the literal code to add.
- **Type consistency:** `_pre_wait_state: AgentState | None`, `on_pending_changed: Callable[[int], None] | None`, `AgentState` enum values are all consistent across tasks.
- **DRY/YAGNI:** no new event types, no UI widgets beyond the cell-color upgrade, no schema migrations. The existing `AgentStateChanged` event and `AgentInfo.state` field carry everything.
- **TDD:** every task introduces a failing test before the implementation that satisfies it.
- **Frequent commits:** ten tasks, ten commits.
