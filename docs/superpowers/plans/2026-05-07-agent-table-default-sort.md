# AgentTable Default Sort Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Default-sort the `AgentTable` widget so agents that need attention surface at the top — `WAITING` first, then `RUNNING`/`IDLE`, then `ERROR`, then `DONE`, with archived agents pinned to the bottom — and keep the order correct as state, archive, and message events arrive.

**Architecture:** Introduce a single pure sort function `sort_agents()` in `patchfeld/agents/sort.py` that owns the priority map and tiebreakers. The widget becomes a thin client: every event handler that could change order routes through one private `_rebuild_sorted()` method that captures the focused agent id, calls `DataTable.clear()`, re-adds rows in sorted order, and restores the cursor. No insertion-at-index or `DataTable.sort()` tricks — straightforward clear-and-rebuild keeps the invariants obvious.

**Tech Stack:** Python 3.11+, Textual `DataTable`, pytest + `textual.app.App.run_test()`.

---

## Background — current behavior on `sort-agents`

| Concern | Current state |
| --- | --- |
| `AgentTable._add_row` | Appends to the bottom in arrival order. |
| `AgentTable._on_state` | Calls `_sync_row(info)` which only updates cells — order never changes when an agent transitions buckets. |
| `AgentTable.on_mount` seed | Iterates `AgentsIndex.load()` and `_add_row`s in disk order (which is upsert order). |
| `AgentTable._rebuild_rows` | Exists, used only for `_show_archived` toggle; iterates `self._infos.values()` (insertion order). |
| `AgentManager.list_infos` | Returns `[s.info for s in self._sessions.values()]` (insertion order). Not consumed by `AgentTable` today; leave alone. |
| `AgentsIndex` | Persists in upsert order; no implicit ordering contract. |
| `AgentState` enum on this branch | `IDLE`, `RUNNING`, `WAITING`, `DONE`, `ERROR`. **`AWAITING_PERMISSION` is NOT on this branch** (those commits live on the approval-modals branch). The sort priority map must be defined so adding new states later is a one-line change. |
| Tests pinning row order | None of the existing tests in `tests/test_agent_table_widget.py` assert a specific row order — they only assert `row_count`. The seed test at line 235 asserts a `set` of keys, not a list. Order updates will not break existing assertions. |

## Decisions (and the tradeoffs behind them)

### 1. State priority map

```
WAITING  → 0   # blocked on user/orchestrator response — top priority
RUNNING  → 1   # actively producing
IDLE     → 2   # live but between turns; rare, transient
ERROR    → 3   # terminal, needs triage, but DONE is more common so put ERROR above DONE
DONE     → 4   # terminal, least interesting
```

`IDLE` is not in the user's brief but exists in the enum. Place it adjacent to `RUNNING` (priority 2) because semantically it's "session is alive and warm" — putting it next to the other live states keeps the live cluster contiguous.

`ERROR` above `DONE` because errors usually need triage; users want them surfaced ahead of the long tail of completed agents.

**Future-proofing:** When `AWAITING_PERMISSION` lands from the other branch, slot it at priority 0 alongside `WAITING` (or 0.5 between `WAITING` and `RUNNING`) — it's the same "blocked, needs human" semantic. Document this so the merger knows what to do.

### 2. Tiebreaker within a bucket

For non-`DONE` buckets: **`last_activity` descending** (most recent activity first). This puts the agent the user just touched at the top of its bucket.

For `DONE` bucket: **`ended_at` descending**, falling back to `last_activity` descending if `ended_at` is `None`. Why a different key for `DONE`?

- `last_activity` is bumped by every transcript message, including tool results and tail-end output. For terminal agents the value is "the last byte we recorded," which is fine.
- `ended_at` is set explicitly when the session moves to `DONE` (or to `ERROR` via `reconcile_orphans`). It's the canonical "this agent finished at" timestamp.
- In practice the two are within milliseconds of each other for a clean run, so the difference is mostly cosmetic. **Recommendation: use `ended_at` for `DONE` because it's the more meaningful timestamp for terminal rows and it survives any future tweaks to what counts as "activity."** If `ended_at` is missing (legacy record), fall back to `last_activity` so we never crash on `None`.
- Tradeoff acknowledged: a uniform `last_activity` key everywhere would be simpler (one branch fewer in the comparator). The branchier code is worth it for the clearer semantics on terminal rows.

**Final tiebreaker** (when both timestamps tie): `started_at` ascending — preserves stable spawn order for visually identical rows.

### 3. Archived agents

When `_show_archived` is `True`, archived rows go **at the very bottom** regardless of state. Among archived agents, sort by `ended_at` desc → `started_at` asc (same as `DONE` bucket, just shifted lower).

This is consistent with the existing archive UX: archived means "I'm done thinking about this agent" — when they're surfaced, they're below everything live. Confirmed against `_render_cells` which already paints archived rows differently (status cell shows `archived`, name suffix `(archived)`).

### 4. DataTable mechanics — why clear-and-rebuild

Three options were considered:

| Option | Pros | Cons | Verdict |
| --- | --- | --- | --- |
| `DataTable.sort(*columns, key=...)` | Native API, in-place. | The `key` callable receives only the cell values from the named columns — not arbitrary state. Would require encoding the priority into a hidden cell. Fragile. | **Rejected.** |
| Insert-at-index via private API | Minimal redraw. | No public API; relies on internals (`_data` / `_row_locations`) that vary across Textual versions. | **Rejected.** |
| `clear()` + `add_row()` for every visible agent in sorted order | Trivially correct, no internals coupling, deterministic. | O(N) redraw per event. | **Chosen.** |

The agent table is small in practice (typically < 50 rows even after weeks of work). O(N) per event is invisible. The clear-and-rebuild path already exists for the archive-toggle case (`_rebuild_rows`); we generalize it.

### 5. When the sort runs

| Event | Action | Rationale |
| --- | --- | --- |
| `on_mount` (after disk seed) | `_rebuild_sorted()` | Initial seed must respect order. |
| `AgentSpawned` | `_rebuild_sorted()` | New agent enters at the right spot, not always the bottom. |
| `AgentStateChanged` | `_rebuild_sorted()` | The whole point — bucket transitions move rows. |
| `AgentArchiveChanged` | `_rebuild_sorted()` | Archived flag changes group placement. |
| `AgentMessageAppended` | `_rebuild_sorted()` | `last_activity` is a tiebreaker; a message can re-rank rows within a bucket. |
| `action_toggle_show_archived` | `_rebuild_sorted()` | Replaces existing `_rebuild_rows()` call. |

This is a deliberate "always rebuild" stance. Smarter strategies (only rebuild on transition that crosses a bucket boundary; only rebuild when comparator output changes) save microseconds at the cost of a maze of edge cases. The simple version is the reliable version.

### 6. Cursor preservation

Every `_rebuild_sorted()` call:

1. Captures `cursor_agent_id = self._cursor_agent_id()` BEFORE any mutation (uses the existing helper at `agent_table.py:148`).
2. Calls `DataTable.clear()`, then `_rows.clear()`, then `add_row()`s in sorted order — same shape as today's `_rebuild_rows()`.
3. After re-add, if `cursor_agent_id` is in `self._rows`, walk the table's row order and call `table.move_cursor(row=index)`. If the focused agent is no longer visible (got archived, or was the only row of its kind that just left), leave the cursor at its default (row 0).

Captured BEFORE mutation because `_cursor_agent_id` reads from `DataTable.coordinate_to_cell_key`, which raises after `clear()`.

### 7. Edge cases — explicit handling

| Case | Behavior |
| --- | --- |
| Empty `_infos` | `sort_agents([])` returns `[]`; `_rebuild_sorted()` no-ops gracefully. |
| Single agent | Sort is trivial; cursor stays at row 0. |
| Two agents with identical `last_activity` | Final tiebreaker `started_at` asc keeps order stable across re-renders. |
| Just-spawned agent with `last_activity == 0.0` | `AgentInfo.__post_init__` already initializes `last_activity = started_at`, so the field is always populated. No special-case needed. |
| State change AND archive flip in the same handler frame | EventBus dispatches synchronously; the second event arrives after the first finishes its rebuild. Each rebuild sees the freshest snapshot in `self._infos`. No coalescing required. |
| Cursor on row about to disappear (archive while `_show_archived=False`) | `cursor_agent_id` won't be in `self._rows` after the rebuild → cursor reset is the desired behavior. |

### 8. Out of scope (deferred to v2)

- **User-overridable column-click sort.** The brief explicitly defers this. Document in the new module's docstring that header-click sort is intentionally not wired up in v1; whoever picks it up later will need to (a) add a `_sort_mode` field to `AgentTable`, (b) plumb header-click → sort mode into the existing `_rebuild_sorted()`, and (c) add a "reset to default" affordance.
- **`AgentManager.list_infos` ordering.** Currently insertion order; leave alone. The sort lives next to its consumer (the widget). Future callers can opt in by importing `sort_agents`.
- **`AgentsIndex` persistence ordering.** No on-disk ordering contract today; we keep it that way.
- **`AWAITING_PERMISSION`.** Not on this branch. The priority map will gain an entry when the approval-modals branch merges. Add a note in the docstring to that effect; the test for the sort function should explicitly comment that the enum has 5 members today and the sort should still be coherent.

## File Structure

| File | Action | Responsibility |
| --- | --- | --- |
| `patchfeld/agents/sort.py` | **Create** | Pure module: `STATE_PRIORITY` dict, `sort_agents(infos: Iterable[AgentInfo]) -> list[AgentInfo]`. No Textual imports. |
| `patchfeld/widgets/agent_table.py` | **Modify** | Replace ad-hoc `_rebuild_rows` and event handlers with a single `_rebuild_sorted()` that delegates ordering to `sort_agents()`. Preserve cursor. |
| `tests/test_agent_sort.py` | **Create** | Pure unit tests on `sort_agents()` — every bucket ordering, tiebreaker, archived placement, edge cases. |
| `tests/test_agent_table_widget.py` | **Modify** | Add widget-level order assertions: order after seed, after spawn, after `RUNNING→DONE` transition, after archive while shown, cursor preservation. Existing tests assert `row_count` only and won't regress. |

## Tasks

Each task ends with a green build (`pytest -q`) and a commit. Tasks are independent enough to execute one at a time.

---

### Task 1: Pure sort function with TDD

**Files:**
- Create: `patchfeld/agents/sort.py`
- Create: `tests/test_agent_sort.py`

- [ ] **Step 1: Write failing test for state-priority ordering**

Create `tests/test_agent_sort.py`:

```python
from patchfeld.agents.sort import sort_agents
from patchfeld.agents.state import AgentInfo, AgentState


def _info(
    id: str,
    state: AgentState = AgentState.RUNNING,
    *,
    started_at: float = 100.0,
    last_activity: float | None = None,
    ended_at: float | None = None,
    archived: bool = False,
) -> AgentInfo:
    return AgentInfo(
        id=id,
        name=f"agent-{id}",
        cwd="/tmp",
        started_at=started_at,
        state=state,
        last_activity=last_activity if last_activity is not None else started_at,
        ended_at=ended_at,
        archived=archived,
    )


def test_state_priority_waiting_running_idle_error_done():
    infos = [
        _info("d", state=AgentState.DONE),
        _info("e", state=AgentState.ERROR),
        _info("i", state=AgentState.IDLE),
        _info("r", state=AgentState.RUNNING),
        _info("w", state=AgentState.WAITING),
    ]
    ordered = [i.id for i in sort_agents(infos)]
    assert ordered == ["w", "r", "i", "e", "d"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_agent_sort.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'patchfeld.agents.sort'`.

- [ ] **Step 3: Implement minimal `sort_agents`**

Create `patchfeld/agents/sort.py`:

```python
"""Default sort order for the AgentTable widget.

The sort surfaces agents that need attention at the top:

  WAITING (blocked on user/orchestrator) → RUNNING/IDLE (live)
  → ERROR (triage) → DONE (terminal, least interesting)

Within a bucket, rows order by recent-activity desc, with `started_at`
asc as a stable final tiebreaker. Archived agents always sink to the
bottom regardless of state.

This is the v1 default sort. User-overridable column-click sort is
deliberately out of scope; see plan
`docs/superpowers/plans/2026-05-07-agent-table-default-sort.md`.

When `AWAITING_PERMISSION` (currently only on the approval-modals
branch) merges, add it to STATE_PRIORITY at priority 0 — same
semantic as WAITING.
"""

from __future__ import annotations

from typing import Iterable

from patchfeld.agents.state import AgentInfo, AgentState

STATE_PRIORITY: dict[AgentState, int] = {
    AgentState.WAITING: 0,
    AgentState.RUNNING: 1,
    AgentState.IDLE: 2,
    AgentState.ERROR: 3,
    AgentState.DONE: 4,
}


def _sort_key(info: AgentInfo) -> tuple:
    # Archived agents sink past every live row regardless of state.
    archived_bucket = 1 if info.archived else 0
    state_bucket = STATE_PRIORITY.get(info.state, len(STATE_PRIORITY))

    # For terminal/archived rows, prefer ended_at as the "when" timestamp;
    # last_activity is the fallback so legacy rows without ended_at still sort.
    if info.archived or info.state == AgentState.DONE:
        when = info.ended_at if info.ended_at is not None else info.last_activity
    else:
        when = info.last_activity

    # Tuple-compare: smaller archived/state buckets win; -when sorts desc;
    # started_at asc breaks ties stably.
    return (archived_bucket, state_bucket, -when, info.started_at)


def sort_agents(infos: Iterable[AgentInfo]) -> list[AgentInfo]:
    """Return `infos` ordered for default AgentTable display.

    Pure: no side effects, no Textual coupling. Caller passes whatever
    snapshot they want sorted (live, persisted, mixed).
    """
    return sorted(infos, key=_sort_key)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_agent_sort.py -v`
Expected: PASS.

- [ ] **Step 5: Add tests for tiebreakers and archived placement**

Append to `tests/test_agent_sort.py`:

```python
def test_within_bucket_last_activity_desc():
    infos = [
        _info("old", state=AgentState.RUNNING, started_at=100.0, last_activity=110.0),
        _info("new", state=AgentState.RUNNING, started_at=100.0, last_activity=200.0),
        _info("mid", state=AgentState.RUNNING, started_at=100.0, last_activity=150.0),
    ]
    ordered = [i.id for i in sort_agents(infos)]
    assert ordered == ["new", "mid", "old"]


def test_done_bucket_uses_ended_at_when_present():
    # Both DONE; the one that ended later sorts first even though its
    # last_activity is older — ended_at is the canonical "finished at".
    a = _info("late", state=AgentState.DONE, last_activity=100.0, ended_at=200.0)
    b = _info("early", state=AgentState.DONE, last_activity=150.0, ended_at=180.0)
    ordered = [i.id for i in sort_agents([b, a])]
    assert ordered == ["late", "early"]


def test_done_bucket_falls_back_to_last_activity_when_ended_at_missing():
    a = _info("a", state=AgentState.DONE, last_activity=200.0, ended_at=None)
    b = _info("b", state=AgentState.DONE, last_activity=100.0, ended_at=None)
    ordered = [i.id for i in sort_agents([b, a])]
    assert ordered == ["a", "b"]


def test_started_at_breaks_full_ties():
    a = _info("first", state=AgentState.RUNNING, started_at=100.0, last_activity=200.0)
    b = _info("second", state=AgentState.RUNNING, started_at=150.0, last_activity=200.0)
    ordered = [i.id for i in sort_agents([b, a])]
    assert ordered == ["first", "second"]


def test_archived_sinks_below_every_live_row():
    infos = [
        _info("archived-running", state=AgentState.RUNNING, archived=True,
              last_activity=999.0),  # very recent — would otherwise top the table
        _info("live-done", state=AgentState.DONE, last_activity=100.0,
              ended_at=100.0),
        _info("live-waiting", state=AgentState.WAITING, last_activity=50.0),
    ]
    ordered = [i.id for i in sort_agents(infos)]
    assert ordered == ["live-waiting", "live-done", "archived-running"]


def test_archived_among_themselves_order_by_ended_at_desc():
    infos = [
        _info("a", state=AgentState.DONE, archived=True, ended_at=100.0),
        _info("b", state=AgentState.DONE, archived=True, ended_at=200.0),
        _info("c", state=AgentState.ERROR, archived=True, ended_at=150.0),
    ]
    ordered = [i.id for i in sort_agents(infos)]
    assert ordered == ["b", "c", "a"]


def test_empty_input():
    assert sort_agents([]) == []


def test_single_agent():
    info = _info("only")
    assert sort_agents([info]) == [info]


def test_just_spawned_agent_has_usable_timestamp():
    # AgentInfo.__post_init__ defaults last_activity to started_at, so
    # the sort key never sees a 0.0/None last_activity for a new agent.
    info = _info("new", state=AgentState.RUNNING, started_at=500.0)
    assert info.last_activity == 500.0
    assert sort_agents([info]) == [info]
```

- [ ] **Step 6: Run all sort tests**

Run: `pytest tests/test_agent_sort.py -v`
Expected: 8 PASSED.

- [ ] **Step 7: Commit**

```bash
git add patchfeld/agents/sort.py tests/test_agent_sort.py
git commit -m "feat(sort): add sort_agents() default ordering for agent table"
```

---

### Task 2: Wire `sort_agents()` into AgentTable

**Files:**
- Modify: `patchfeld/widgets/agent_table.py:76-126,202-210`

- [ ] **Step 1: Write failing test for default order on disk seed**

Append to `tests/test_agent_table_widget.py`:

```python
@pytest.mark.asyncio
async def test_seeded_rows_are_in_default_sort_order(tmp_path: Path):
    # Seed mixes states; expect WAITING > RUNNING > ERROR > DONE order.
    idx = AgentsIndex(cwd=tmp_path)
    idx.save([
        _info("d1", state=AgentState.DONE),
        _info("e1", state=AgentState.ERROR),
        _info("r1", state=AgentState.RUNNING),
        _info("w1", state=AgentState.WAITING),
    ])
    bus = EventBus()
    app = _HostApp(bus, cwd=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one(AgentTable).query_one(DataTable)
        keys = [str(row.value) for row in table.rows.keys()]
        assert keys == ["w1", "r1", "e1", "d1"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_agent_table_widget.py::test_seeded_rows_are_in_default_sort_order -v`
Expected: FAIL — order is `["d1", "e1", "r1", "w1"]` (insertion order from disk).

- [ ] **Step 3: Replace `on_mount` seed and `_rebuild_rows` with sorted variants**

In `patchfeld/widgets/agent_table.py`:

Add the import near the top with the other patchfeld imports:

```python
from patchfeld.agents.sort import sort_agents
```

Replace the body of `on_mount` (the disk-seed loop and bus subscriptions) with:

```python
def on_mount(self) -> None:
    # Seed past agents from disk so a fresh process boot still surfaces
    # the agents the user spawned in the previous session. AgentManager
    # has already reconciled any non-terminal records to ERROR, so what
    # we read here is safe to display as-is.
    cwd = getattr(self.app, "cwd", None)
    if cwd is not None:
        for info in AgentsIndex(cwd=cwd).load():
            if info.id == "orchestrator":
                continue
            # Just record into _infos; the rebuild below renders rows in order.
            self._infos[info.id] = info
        self._rebuild_sorted()

    bus = self._bus or getattr(self.app, "event_bus", None)
    if bus is None:
        return
    self._unsubs.append(bus.subscribe(AgentSpawned, self._on_spawned))
    self._unsubs.append(bus.subscribe(AgentStateChanged, self._on_state))
    self._unsubs.append(bus.subscribe(AgentMessageAppended, self._on_msg))
    self._unsubs.append(
        bus.subscribe(AgentArchiveChanged, self._on_archive_changed)
    )
```

Replace `_rebuild_rows` with `_rebuild_sorted`:

```python
def _rebuild_sorted(self) -> None:
    """Clear and re-add rows from `_infos` in default sort order, honoring
    the visibility filter. Preserves the cursor's focused agent across the
    rebuild so a sort-induced reorder doesn't snap the user back to row 0."""
    table = self.query_one(DataTable)

    # Capture cursor agent BEFORE clear(); coordinate_to_cell_key throws
    # after the table is empty.
    cursor_agent_id = self._cursor_agent_id()

    table.clear()
    self._rows.clear()

    visible = [info for info in self._infos.values() if self._is_visible(info)]
    for info in sort_agents(visible):
        table.add_row(*self._render_cells(info), key=info.id)
        self._rows[info.id] = info.id

    # Restore cursor onto the same agent if it's still visible.
    if cursor_agent_id is not None and cursor_agent_id in self._rows:
        for index, row_key in enumerate(table.rows.keys()):
            if str(row_key.value) == cursor_agent_id:
                table.move_cursor(row=index)
                break
```

Update `action_toggle_show_archived` to call the new method:

```python
def action_toggle_show_archived(self) -> None:
    self._show_archived = not self._show_archived
    self._rebuild_sorted()
```

- [ ] **Step 4: Run the seed test and the existing widget tests**

Run: `pytest tests/test_agent_table_widget.py -v`
Expected: all tests pass, including the new `test_seeded_rows_are_in_default_sort_order`.

If any existing test fails because of order, the failure indicates either (a) a real bug to fix or (b) an implicit-order assertion that needs updating. The known existing tests in this file assert `row_count` and set-membership only, so they should not break.

- [ ] **Step 5: Commit**

```bash
git add patchfeld/widgets/agent_table.py tests/test_agent_table_widget.py
git commit -m "feat(agent-table): apply default sort on disk seed and rebuild"
```

---

### Task 3: Hook `_rebuild_sorted()` into spawn / state / archive events

**Files:**
- Modify: `patchfeld/widgets/agent_table.py` — event handler bodies

- [ ] **Step 1: Write failing test for state transition reordering**

Append to `tests/test_agent_table_widget.py`:

```python
@pytest.mark.asyncio
async def test_state_change_running_to_done_moves_row_to_bottom():
    # Two agents start RUNNING; when one finishes, the DONE row should
    # drop below the still-RUNNING row.
    bus = EventBus()
    app = _HostApp(bus)
    async with app.run_test() as pilot:
        await pilot.pause()
        a = _info("a", state=AgentState.RUNNING)
        b = _info("b", state=AgentState.RUNNING)
        bus.publish(AgentSpawned(info=a))
        bus.publish(AgentSpawned(info=b))
        await pilot.pause()

        # Flip "a" to DONE.
        a_done = dataclasses.replace(a, state=AgentState.DONE, ended_at=200.0)
        bus.publish(AgentStateChanged(info=a_done, old_state=AgentState.RUNNING))
        await pilot.pause()

        table = app.query_one(AgentTable).query_one(DataTable)
        keys = [str(row.value) for row in table.rows.keys()]
        # "b" still RUNNING ⇒ priority 1; "a" now DONE ⇒ priority 4.
        assert keys == ["b", "a"]


@pytest.mark.asyncio
async def test_spawn_inserts_at_correct_priority_position():
    # Existing DONE agent on top should be displaced when a WAITING
    # agent spawns.
    bus = EventBus()
    app = _HostApp(bus)
    async with app.run_test() as pilot:
        await pilot.pause()
        bus.publish(AgentSpawned(info=_info("d", state=AgentState.DONE)))
        await pilot.pause()
        bus.publish(AgentSpawned(info=_info("w", state=AgentState.WAITING)))
        await pilot.pause()

        table = app.query_one(AgentTable).query_one(DataTable)
        keys = [str(row.value) for row in table.rows.keys()]
        assert keys == ["w", "d"]


@pytest.mark.asyncio
async def test_archived_row_sinks_to_bottom_when_visible():
    bus = EventBus()
    app = _HostApp(bus)
    async with app.run_test() as pilot:
        await pilot.pause()
        bus.publish(AgentSpawned(info=_info("live", state=AgentState.DONE)))
        bus.publish(AgentSpawned(info=_info("arch", state=AgentState.WAITING,
                                            archived=True)))
        await pilot.pause()

        table = app.query_one(AgentTable).query_one(DataTable)
        await pilot.press("a")  # show archived
        await pilot.pause()

        keys = [str(row.value) for row in table.rows.keys()]
        # Even though "arch" is WAITING (priority 0), it's archived ⇒ last.
        assert keys == ["live", "arch"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_agent_table_widget.py::test_state_change_running_to_done_moves_row_to_bottom tests/test_agent_table_widget.py::test_spawn_inserts_at_correct_priority_position tests/test_agent_table_widget.py::test_archived_row_sinks_to_bottom_when_visible -v`
Expected: all three FAIL — current handlers `_add_row`/`_sync_row` don't reorder.

- [ ] **Step 3: Reroute event handlers through `_rebuild_sorted()`**

In `patchfeld/widgets/agent_table.py`, replace the three event handlers:

```python
def _on_spawned(self, event: AgentSpawned) -> None:
    # Record the new agent and rebuild so it lands at the right priority.
    self._infos[event.info.id] = event.info
    self._rebuild_sorted()

def _on_state(self, event: AgentStateChanged) -> None:
    # Preserve any archived flag we already know about — AgentStateChanged
    # is emitted by AgentSession with the live info, but the SDK side
    # doesn't touch `archived`, so an existing snapshot's flag is the
    # source of truth.
    prev = self._infos.get(event.info.id)
    if prev is not None and prev.archived and not event.info.archived:
        event.info.archived = True
    self._infos[event.info.id] = event.info
    self._rebuild_sorted()

def _on_archive_changed(self, event: AgentArchiveChanged) -> None:
    self._infos[event.info.id] = event.info
    self._rebuild_sorted()
```

Note: `_add_row`, `_remove_row`, `_sync_row`, and `_refresh_cells` are no longer reached from event handlers. Delete `_sync_row`, `_refresh_cells`, `_add_row`, and `_remove_row` to keep the widget lean (they only existed to support the old in-place-update path; the new rebuild path renders all cells from `_infos` every time).

- [ ] **Step 4: Run the new tests and the full widget suite**

Run: `pytest tests/test_agent_table_widget.py -v`
Expected: all PASS, including new ordering tests.

- [ ] **Step 5: Commit**

```bash
git add patchfeld/widgets/agent_table.py tests/test_agent_table_widget.py
git commit -m "feat(agent-table): re-sort on spawn / state / archive events"
```

---

### Task 4: Re-sort on `AgentMessageAppended` for last_activity tiebreaker

**Files:**
- Modify: `patchfeld/widgets/agent_table.py` — `_on_msg` handler

- [ ] **Step 1: Write failing test**

Append to `tests/test_agent_table_widget.py`:

```python
@pytest.mark.asyncio
async def test_message_bumps_agent_to_top_of_its_bucket():
    # Two RUNNING agents; the one that just received a message should
    # rise to the top of the RUNNING bucket via the last_activity
    # tiebreaker. To get the bump, we publish a state event whose info
    # carries the new last_activity (since AgentMessageAppended itself
    # doesn't carry the AgentInfo — but _on_msg uses the cached info).
    bus = EventBus()
    app = _HostApp(bus)
    async with app.run_test() as pilot:
        await pilot.pause()
        a = _info("a", state=AgentState.RUNNING)
        a.last_activity = 100.0
        b = _info("b", state=AgentState.RUNNING)
        b.last_activity = 200.0
        bus.publish(AgentSpawned(info=a))
        bus.publish(AgentSpawned(info=b))
        await pilot.pause()

        table = app.query_one(AgentTable).query_one(DataTable)
        # b is more recent ⇒ b first.
        keys = [str(row.value) for row in table.rows.keys()]
        assert keys == ["b", "a"]

        # Now a gets a message; bump its last_activity past b's and
        # republish via AgentStateChanged (same state, fresher timestamp).
        a.last_activity = 300.0
        bus.publish(AgentMessageAppended(
            agent_id="a", role="assistant", text="hello",
        ))
        await pilot.pause()
        keys = [str(row.value) for row in table.rows.keys()]
        assert keys == ["a", "b"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_agent_table_widget.py::test_message_bumps_agent_to_top_of_its_bucket -v`
Expected: FAIL — current `_on_msg` only updates the last-action cell.

- [ ] **Step 3: Reroute `_on_msg` through `_rebuild_sorted()`**

In `patchfeld/widgets/agent_table.py`, replace `_on_msg`:

```python
def _on_msg(self, event: AgentMessageAppended) -> None:
    self._last_actions[event.agent_id] = f"[{event.role}] {event.text[:60]}"
    if event.agent_id in self._infos:
        # Sort key depends on last_activity; rebuild so the row may bubble
        # up within its bucket. The cached AgentInfo is updated elsewhere
        # (AgentSession sets last_activity before publishing state events);
        # here we just reflect the freshest snapshot we have.
        self._rebuild_sorted()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_agent_table_widget.py::test_message_bumps_agent_to_top_of_its_bucket -v`
Expected: PASS.

- [ ] **Step 5: Run the full widget + sort suites**

Run: `pytest tests/test_agent_table_widget.py tests/test_agent_sort.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add patchfeld/widgets/agent_table.py tests/test_agent_table_widget.py
git commit -m "feat(agent-table): re-sort on message events for last-activity tiebreaker"
```

---

### Task 5: Cursor preservation across rebuild

**Files:**
- Modify: `patchfeld/widgets/agent_table.py` (only if cursor restore wasn't already wired in Task 2; if it was, this task is the test-only verification)

- [ ] **Step 1: Write failing test**

Append to `tests/test_agent_table_widget.py`:

```python
@pytest.mark.asyncio
async def test_cursor_follows_agent_across_state_change_reorder():
    # Two RUNNING agents; cursor on "a"; flip "b" to DONE so order
    # changes; cursor should still be on "a".
    bus = EventBus()
    app = _HostApp(bus)
    async with app.run_test() as pilot:
        await pilot.pause()
        a = _info("a", state=AgentState.RUNNING)
        a.last_activity = 200.0
        b = _info("b", state=AgentState.RUNNING)
        b.last_activity = 100.0
        bus.publish(AgentSpawned(info=a))
        bus.publish(AgentSpawned(info=b))
        await pilot.pause()

        widget = app.query_one(AgentTable)
        table = widget.query_one(DataTable)
        table.focus()
        await pilot.pause()
        # Initial order: ["a", "b"] (a is more recent). Cursor at row 0 → "a".
        assert widget._cursor_agent_id() == "a"

        # Bump "b" — DONE drops it to the bottom. Order becomes ["a", "b"]
        # still (a is RUNNING; b is DONE). Cursor must still be on "a".
        b_done = dataclasses.replace(b, state=AgentState.DONE, ended_at=300.0)
        bus.publish(AgentStateChanged(info=b_done, old_state=AgentState.RUNNING))
        await pilot.pause()

        assert widget._cursor_agent_id() == "a"


@pytest.mark.asyncio
async def test_cursor_resets_when_focused_agent_archived_off_screen():
    # Cursor on "a"; archive "a" with archived hidden; cursor's prior
    # agent is gone — table should not crash and should land cursor on
    # whichever row is at index 0 (or have no cursor if empty).
    bus = EventBus()
    infos = {"a": _info("a"), "b": _info("b")}
    manager = _StubManager(bus, infos)
    app = _HostApp(bus, manager=manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        bus.publish(AgentSpawned(info=infos["a"]))
        bus.publish(AgentSpawned(info=infos["b"]))
        await pilot.pause()

        widget = app.query_one(AgentTable)
        table = widget.query_one(DataTable)
        table.focus()
        await pilot.pause()
        # Move cursor to row 0 (whatever sort order produced); we don't
        # care which agent — just that the table doesn't blow up after
        # archiving the one at the cursor.
        focused = widget._cursor_agent_id()
        assert focused in ("a", "b")
        manager.set_archived(focused, archived=True)
        await pilot.pause()

        # Only the un-archived row remains (archived hidden by default).
        assert table.row_count == 1
        # Cursor lands on the surviving row without raising.
        assert widget._cursor_agent_id() in ("a", "b")
```

- [ ] **Step 2: Run tests**

Run: `pytest tests/test_agent_table_widget.py::test_cursor_follows_agent_across_state_change_reorder tests/test_agent_table_widget.py::test_cursor_resets_when_focused_agent_archived_off_screen -v`
Expected: PASS if Task 2 already added the cursor restoration block; FAIL otherwise (revisit `_rebuild_sorted` and add the restore block from Task 2 Step 3).

- [ ] **Step 3: If anything failed, fix `_rebuild_sorted` cursor restoration**

Re-read the `_rebuild_sorted` body from Task 2 and confirm the post-loop cursor-restore code is present. If absent, add:

```python
if cursor_agent_id is not None and cursor_agent_id in self._rows:
    for index, row_key in enumerate(table.rows.keys()):
        if str(row_key.value) == cursor_agent_id:
            table.move_cursor(row=index)
            break
```

- [ ] **Step 4: Run full widget + sort suites**

Run: `pytest tests/test_agent_table_widget.py tests/test_agent_sort.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add patchfeld/widgets/agent_table.py tests/test_agent_table_widget.py
git commit -m "test(agent-table): cover cursor preservation across sort reorder"
```

---

### Task 6: Final regression sweep

**Files:** none modified — verification only.

- [ ] **Step 1: Run the entire test suite**

Run: `pytest -q`
Expected: all green. If any test outside the agent-table/sort modules fails, investigate — the changes here are localized but a downstream consumer of `AgentTable` order (e.g., a layout smoke test) could in principle have an implicit order assumption.

- [ ] **Step 2: Hand-verify with a quick `grep` for now-orphaned helpers**

Run via Grep tool: search the repo for `_sync_row`, `_refresh_cells`, `_add_row`, `_remove_row`, `_rebuild_rows` — confirm no callers remain inside `agent_table.py` or its tests beyond the deletions.

If any external caller (e.g., a sibling widget) referenced one of these private helpers, restore the helper as a thin wrapper around `_rebuild_sorted()` and add a TODO to clean up the caller in a follow-up.

- [ ] **Step 3: No additional commit unless Step 2 surfaced a leftover.**

---

## Self-Review checklist

- ✅ **Spec coverage:**
  - State priority order (WAITING > RUNNING > ERROR > DONE) — Task 1, `STATE_PRIORITY`.
  - Within-bucket tiebreaker recommendation with tradeoff — Decisions §2.
  - Sort key location reusable from widget and manager — `patchfeld/agents/sort.py`.
  - Sort runs at all three places (seed, spawn, state change) plus archive and message — Tasks 2–4.
  - DataTable mechanics with reasoning — Decisions §4.
  - User-overridable column-click sort deferred to follow-up — Decisions §8.
  - Archived agents pinned to bottom — Decisions §3, Task 3 test.
  - Edge cases enumerated — Decisions §7.
  - Affected files listed — File Structure table.
  - Test strategy: pure unit + widget — Tasks 1, 2, 3, 4, 5.
  - Implementation order: 6 numbered tasks, each ending green — Tasks 1–6.
- ✅ **No placeholders:** every task contains real code and a real expected outcome.
- ✅ **Type consistency:** `sort_agents` is `Iterable[AgentInfo] -> list[AgentInfo]` everywhere it's referenced; `_rebuild_sorted` is the single private method name across all tasks; `STATE_PRIORITY` is the single source of truth.
- ✅ **AWAITING_PERMISSION on this branch:** confirmed absent via `git log` on `sort-agents`; documented as a one-line follow-up in the sort module docstring.
