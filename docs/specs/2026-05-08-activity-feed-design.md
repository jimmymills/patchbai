# Activity Feed — Design

**Date:** 2026-05-08
**Status:** Approved (pre-implementation)

## Problem

`ActivityFeed` is currently a placeholder widget that renders the literal text `Activity feed — empty`. Nothing publishes to it, nothing reads from it. The widget is wired into `build_default_registry()` and appears in the dashboard layout, so users mount it and see only the placeholder string.

We want a real event stream: a chronological view over what's happening in the running app — agents, tabs, layouts, the orchestrator — with enough flexibility to serve four distinct use cases without forking the widget.

## Goals

- Replace the placeholder with a working event stream.
- Support four use modes: **audit**, **agents**, **notifications**, **debug**.
- Mode is per-instance: two ActivityFeed panels can show different modes simultaneously.
- Click-through navigation for agent-related rows and a few other actionable kinds.
- Auto-follow scroll with pause-on-scroll-up.
- Backlog visible when mounting the panel mid-session.

## Non-goals (v1)

- Disk persistence (history resets on app restart).
- Search/filter text input within a mode.
- Copy-to-clipboard on rows.
- Hotkey to cycle modes (chips only).
- Rendering tool-call arguments inline.
- Surfacing `AgentTokensTouched` / `StatsUpdated` (StatusBar already covers this).

## Architecture

Two pieces:

### `ActivityLog` (singleton on `PatchfeldApp`)

- Subscribes to a curated set of `EventBus` events at construction time.
- Normalizes each into an `ActivityEntry`:
  ```python
  @dataclass(frozen=True)
  class ActivityEntry:
      timestamp: datetime
      kind: str            # e.g. "agent.spawned", "layout.failed"
      summary: str         # short, single-line description
      detail: str | None   # richer body for expanded/card variants
      agent_id: str | None # for click-through
      tab_id: str | None   # for tab.* kinds
      raw: object          # the original event object, for debugging
  ```
- Stores entries in a `collections.deque(maxlen=500)`.
- Publishes a new `ActivityLogged(entry)` event after each append.
- Exposes `entries() -> tuple[ActivityEntry, ...]` (snapshot) for new widgets that need backlog at mount time.
- Captures every event regardless of whether any `ActivityFeed` widget is mounted — modes are a presentation filter, not a capture filter.

### `ActivityFeed` (Textual `Container`)

- Replaces the current placeholder.
- Subscribes to `ActivityLogged`. On each event:
  - If `entry.kind` passes the current mode's filter, mount one `_ActivityRow` at the bottom and drop the topmost row if displayed-row count exceeds the buffer cap. (Displayed cap = same 500 — a mode showing fewer kinds may show fewer rows, never more.)
- On mount: pulls `app.activity_log.entries()` and renders the slice that matches the current mode.
- Auto-follow: scrolls to the bottom on each new row unless the user has scrolled up — track this with a `_paused: bool` toggled on user scroll events; clears when the user scrolls back to the bottom.
- Children:
  1. `_ModeChips` (Horizontal of clickable Static buttons) docked at top.
  2. A `VerticalScroll` containing the rows.

## Mode → Event Mapping

The singleton captures the union; modes filter `entry.kind`. Coverage:

| Kind | Source | audit | agents | notifs | debug |
|---|---|:-:|:-:|:-:|:-:|
| `agent.spawned` | `AgentSpawned` | ✓ | ✓ |  | ✓ |
| `agent.state` | `AgentStateChanged` (non-terminal) | ✓ | ✓ |  | ✓ |
| `agent.done` | `AgentStateChanged` (DONE / ERROR — `AgentState.is_terminal`) | ✓ | ✓ | ✓ | ✓ |
| `agent.message` | `AgentMessageAppended` (role ∈ {user, assistant}) |  | ✓ |  | ✓ |
| `agent.tool` | `AgentMessageAppended` (role ∈ {tool_use, tool_result}) |  |  |  | ✓ |
| `agent.ask` | `AgentRequestedUserInput` | ✓ | ✓ | ✓ | ✓ |
| `agent.notify` | `AgentNotifiedOrchestrator` | ✓ | ✓ | ✓ | ✓ |
| `agent.archive` | `AgentArchiveChanged` | ✓ | ✓ |  | ✓ |
| `orch.user` | `UserMessageToOrchestrator` | ✓ |  |  | ✓ |
| `orch.reply` | `OrchestratorReply` | ✓ |  |  | ✓ |
| `orch.session` | `OrchestratorSessionSwitched` | ✓ |  |  | ✓ |
| `layout.applied` | `LayoutApplied` | ✓ |  |  | ✓ |
| `layout.failed` | `LayoutFailed` | ✓ |  | ✓ | ✓ |
| `tab.added` | `TabAdded` | ✓ |  |  | ✓ |
| `tab.closed` | `TabClosed` | ✓ |  |  | ✓ |
| `tab.switched` | `TabSwitched` |  |  |  | ✓ |
| `workspace.cwd` | `WorkspaceCwdChanged` | ✓ |  | ✓ | ✓ |
| `file.selected` | `FileSelected` |  |  |  | ✓ |

**Derivation: `agent.done`.** When `AgentStateChanged` lands a terminal state (`DONE`, `ERROR`, `CANCELLED`), the singleton emits `agent.done` instead of `agent.state`. Non-terminal transitions emit `agent.state`. This lets `notifs` mode surface completions/errors without subscribing to every state tick.

**Excluded.** `AgentTokensTouched` and `StatsUpdated` are intentionally not captured — too noisy and already represented in StatusBar.

## Row Rendering

Three variants. All share a left gutter `[HH:MM:SS]` timestamp and a colored kind chip.

**Compact** — one line. For: `tab.added`, `tab.closed`, `tab.switched`, `layout.applied`, `workspace.cwd`, `agent.state`, `agent.archive`, `agent.done` (when state is `DONE`), `file.selected`, `agent.tool`, `orch.session`.
```
[15:42:01] tab.added         "Files"
[15:42:14] agent.state       research-bot: RUNNING → IDLE
```

**Expanded** — timestamp/chip line + indented body. For: `orch.user`, `orch.reply`, `agent.message`, `agent.notify`, `agent.spawned` (with cwd/model).
```
[15:43:02] agent.message     research-bot
            ↳ Found 3 candidate files. Investigating…
```
Body wraps to widget width; truncates at ~3 lines with `…`.

**Card** — bordered, multi-line, attention-grabbing. For: `agent.ask`, `layout.failed`, and `agent.done` when state is `ERROR`. Border color follows severity (`$warning` / `$error`).
```
┌─ agent.ask · research-bot ──────────────┐
│ Should I overwrite the existing file?   │
│ (orchestrator is waiting on this reply) │
└─────────────────────────────────────────┘
```

A single `_ActivityRow` Static-derived widget renders all three variants based on a module-level `_VARIANT: dict[str, Variant]` lookup keyed on `entry.kind`.

## Mode Chips & Persistence

A `_ModeChips` Horizontal docked at the top of the panel: **Audit · Agents · Notifs · Debug**. Active mode is highlighted via a CSS class.

**Initial mode resolution:**
1. `props.mode` from layout JSON (`{"widget": "ActivityFeed", "props": {"mode": "agents"}}`).
2. Fallback to `"audit"` if no prop.

**On chip click:**
1. Update the widget's `mode` reactive → triggers re-filter and re-render of the visible rows.
2. Persist back to the layout: walk the active tab's `LayoutSpec`, find the panel entry by id (the widget's `panel-{id}` corresponds to a Panel node), set its `props.mode = new_mode`, then call `app._apply_to_tab(tab_id, new_spec)`. This is the same validate-then-save path Splitter resizes use.

Mode persistence is per-panel, so two ActivityFeed panels can have different modes saved independently.

## Click-Through Targets

| Kind | Action |
|---|---|
| `agent.*` (any agent kind with `agent_id`) | Publish `AgentFocusRequested(agent_id)`. AgentTable subscribes and selects + scrolls to that row. If no AgentTable is mounted in any tab, fall back to `app.push_screen(TranscriptScreen(agent_id=...))`. |
| `layout.failed` | `app.notify(entry.detail, severity="error")` (Textual toast). |
| `tab.added` | Switch to that tab via `tc.active = f"tab-{entry.tab_id}"`. |
| `orch.session` | Focus the OrchestratorChat input if any are mounted (`app.query("OrchestratorChat #orch-input").first().focus()`). |
| All other kinds | Non-interactive — no cursor change, no on-click reaction. |

A module-level `_CLICK_HANDLERS: dict[str, Callable[[App, ActivityEntry], None]]` table drives this. `_ActivityRow.on_click` looks up the handler and calls it; missing key = non-interactive. Rows with handlers add a CSS class for hover styling.

`AgentFocusRequested(agent_id: str)` is a new event type added to `patchfeld/events.py`. AgentTable gains a subscription handler in the same change.

## Files

**New:**
- `patchfeld/activity/__init__.py` — package marker.
- `patchfeld/activity/log.py` — `ActivityEntry`, `ActivityLog`, kind derivation logic.
- `patchfeld/widgets/activity_feed.py` — `ActivityFeed`, `_ModeChips`, `_ActivityRow`, `_VARIANT`, `_CLICK_HANDLERS`.

**Modified:**
- `patchfeld/events.py` — add `ActivityLogged(entry)` and `AgentFocusRequested(agent_id)`.
- `patchfeld/app.py` — instantiate `self.activity_log = ActivityLog(self.event_bus)` in `__init__`.
- `patchfeld/widgets/agent_table.py` — subscribe to `AgentFocusRequested`, select & scroll the matching row.
- `patchfeld/widgets/placeholders.py` — remove `ActivityFeed`. If the file ends up empty, delete it; otherwise leave the rest intact. (Verify at implementation time.)
- The widget registry import in `patchfeld/app.py` switches from `placeholders.ActivityFeed` to `activity_feed.ActivityFeed`.

**No schema changes** to `workspace.json` or any layout file — `props.mode` is just a free-form string in the existing `props` dict.

## Testing

**`tests/test_activity_log.py`** (pure logic, no Textual):
- Each captured event produces an `ActivityEntry` with the expected kind, summary, and any extracted ids.
- `agent.done` derivation: `AgentStateChanged` with terminal state emits `agent.done`; non-terminal emits `agent.state`.
- `AgentTokensTouched` and `StatsUpdated` are dropped (no entry created, no `ActivityLogged` published).
- Ring eviction: pushing > 500 events keeps the most recent 500.
- `ActivityLogged` is published exactly once per accepted event, with the new entry attached.

**`tests/test_activity_feed_widget.py`** (Pilot):
- Backlog: mounting an ActivityFeed after some events have been logged shows them, filtered by mode.
- Mode prop is honored on mount.
- Clicking a chip changes the displayed rows AND persists `props.mode` into `workspace.json`.
- Clicking an agent-kind row publishes `AgentFocusRequested` (subscribe a probe and assert).
- Clicking a `layout.failed` row triggers `app.notify` with `severity="error"` (probe the notification list).
- Clicking a non-interactive kind (e.g. `tab.switched`) doesn't publish anything.
- Auto-follow: new rows scroll the view to the bottom; scrolling up pauses auto-follow; scrolling back to the bottom resumes it.

**`tests/test_activity_feed_modes.py`** (table-driven):
- For each `(mode, kind)` pair in the spec table, assert the row is visible iff the table marks it ✓.

## Open Questions

None at design-approval time.

## Future Work

- Disk persistence (`activity.jsonl`) with bounded size, replay-on-launch.
- Search box that filters within the active mode by substring on `summary`/`detail`.
- Right-click context menu (copy summary, copy raw event, jump to source).
- Custom modes via a workspace-level mode-definition file (kind allowlists).
