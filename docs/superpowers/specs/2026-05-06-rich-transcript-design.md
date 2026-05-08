# RichTranscript — Claude-Code-style live/collapse transcript

Date: 2026-05-06
Status: Design approved, ready for implementation plan

## Problem

The orchestrator chat panel (`OrchestratorChat`) and the per-child-agent panel
(`AgentTranscript`) currently render every model output — assistant text, tool
use, tool results — as a flat stack of single-line `Static`s in a
`VerticalScroll`. The orchestrator panel additionally drops `thinking` blocks
on the floor (`OrchestratorSession._on_message_appended` only handles
`assistant` / `tool_use` / `tool_result`).

The result: long tool sequences look like a wall of `[tool use] …` /
`[tool result] …` lines, the user has no clear idea what step the agent is on,
and once a turn finishes, the actual response is buried in scrollback.

We want behavior closer to the regular Claude Code CLI: tool calls and
thinking are visible while running, then collapse down on completion so the
final response stands out.

## Goals

- Group output by *turn* (one user prompt → all subsequent steps → final response).
- While a turn is running:
  - Tool calls render as foldables, **expanded by default**, with a spinner in
    the title and full args/result inside.
  - Thinking blocks stream live into a foldable group, **expanded by default**.
- When a turn completes:
  - Each tool foldable auto-collapses to a one-line summary.
  - Each thinking group auto-collapses, retitled `Thought for {Xs}`.
  - Final assistant text remains full-width and prominent.
- Both `OrchestratorChat` and `AgentTranscript` get this behavior via a single
  shared widget (`RichTranscript`). The two outer widgets keep their public API
  (constructors, IDs, layout-registry names) untouched so layout YAML, the
  registry, and existing tests don't churn.

## Non-goals

- Streaming partial text within a single `TextBlock` (the SDK delivers blocks
  whole; we render them whole).
- A global "expand/collapse all" keybinding. Per-foldable Enter is enough.
- Persisting per-foldable expand state across remounts. On remount, history is
  replayed as completed turns (all foldables collapsed).
- Reworking child-agent → orchestrator notify/ask flow. Synthetic messages
  continue to come in via `UserMessageToOrchestrator` and naturally appear as
  user-prompt rows opening a new turn.

## Architecture

### New module: `patchbai/widgets/rich_transcript.py`

Exports one public class:

- `RichTranscript(Vertical)` — a scrollable transcript that subscribes to
  `AgentMessageAppended` (filtered by `agent_id`) and `AgentStateChanged`, and
  renders turns containing the sub-widgets defined below.

And three module-private sub-widgets:

- `_TurnContainer(Vertical)` — owns one turn.
- `_ThinkingGroup(Collapsible)` — one contiguous run of thinking blocks.
- `_ToolCall(Collapsible)` — one tool invocation + its result.

### Refactor: `OrchestratorChat` and `AgentTranscript`

Both become thin shells:

```
OrchestratorChat / AgentTranscript
├── RichTranscript(agent_id=...)   # the new widget
└── Input                          # the existing input box, dock=bottom
```

Their constructors, the `Input` IDs (`#orch-input`, `#transcript-input`),
class names, and input-submit behavior are preserved. The internal scroll
IDs (`#orch-messages`, `#transcript-scroll`) and the `_append_line` helper
go away — neither is referenced by tests, and rendering moves into
`RichTranscript`. `AgentTranscript.rendered_text()` is *kept* as a
test-helper façade that delegates to `RichTranscript.rendered_text()` (a
walk of the inner widget tree concatenating all visible Rich `Text`), so
`tests/test_agent_transcript_widget.py` continues to pass without
modification.

`OrchestratorChat`'s preload-from-disk path moves into `RichTranscript`'s
`on_mount`: read the on-disk transcript via `OrchestratorTranscript` (or
`AgentTranscript` store, depending on `agent_id`) and replay each entry as a
completed turn.

### Event-flow simplification

Today, `OrchestratorSession._on_message_appended` re-publishes a string-only
`OrchestratorReply` for assistant text, tool use (with a `[tool use] …`
prefix), and tool results (with a `[tool result] …` prefix and 240-char
truncation) so `OrchestratorChat` can render it. With `RichTranscript`
subscribing directly to `AgentMessageAppended`, the prefixed/truncated
strings are dead weight. We:

- Drop the `[tool use]` and `[tool result]` `OrchestratorReply` re-publishes.
- **Keep** the assistant-text `OrchestratorReply` re-publish — existing tests
  (`test_orchestrator_session.py`, `test_orchestrator_session_serializes.py`)
  assert against it as the public "the orchestrator said something" signal.
- Remove the `OrchestratorReply` subscription in `OrchestratorChat` (it now
  drives off `AgentMessageAppended` instead).

`AgentTranscript` already subscribes directly to `AgentMessageAppended`, so no
upstream changes are needed for it.

### Event-schema extension

`AgentMessageAppended` (in `patchbai/events.py`) gains two optional fields:

```python
@dataclass(frozen=True)
class AgentMessageAppended:
    agent_id: str
    role: str
    text: str
    tool_id: str | None = None       # set for role in {"tool_use", "tool_result"}
    tool_name: str | None = None     # set for role == "tool_use"
```

`AgentSession._handle_message` in `patchbai/agents/session.py` is updated to
populate these fields:

- For `ToolUseBlock`: `tool_id=block.id`, `tool_name=block.name`.
- For `ToolResultBlock`: `tool_id=block.tool_use_id`.

`TranscriptEntry` (the on-disk record in `patchbai/persistence/transcript_store.py`)
also gains the same optional fields, with serialization defaulting to absent
when `None`. Old transcripts without those fields continue to read fine —
`tool_id`/`tool_name` are simply `None`, and on replay we fall back to the
existing "show as a single muted line" behavior described in the *History
replay* section below.

The defaults are `None`, so any caller constructing the dataclass positionally
or using only the original three fields continues to work.

## Widget internals

### `_TurnContainer(Vertical)`

State: `running` | `done` | `error`. The state drives a CSS class on the
container itself (`turn-running`, `turn-done`, `turn-error`) which controls
the left-border accent.

API:
- `__init__(self, user_text: str)` — mounts the user-prompt `Static` (rendered
  as Rich `Text` with bold "you:" prefix).
- `add_thinking(text: str)` — if the most recent step is a `_ThinkingGroup`,
  appends to it; otherwise mounts a new `_ThinkingGroup` and starts there.
- `add_tool_call(tool_id, tool_name, args)` — mounts a new `_ToolCall`,
  records `tool_id → widget` in an internal dict.
- `attach_tool_result(tool_id, content)` — looks up the `_ToolCall` by
  `tool_id` and calls its `attach_result(content)`. If no match (defensive
  guard against malformed transcripts), mount a free-floating `_ToolCall`
  with no args and the result attached.
- `add_text(text)` — mounts a new `Static` styled `-final` (bold "claude:"
  prefix).
- `mark_done()` / `mark_error()` — for each child step widget, call its
  own `mark_done()` / `mark_error()` (which stops its spinner and collapses
  it). Flip own state class.

### `_ThinkingGroup(Collapsible)`

- Title: `⠋ Thinking…` while running (spinner glyph cycled by a
  `set_interval(0.08, ...)` timer); `Thought for {Xs}` once `mark_done()`
  has been called (timer cancelled, glyph removed).
- Body: a single `Static` whose backing Rich `Text` is appended to as new
  thinking blocks arrive. Appending is in-place — we mutate the existing
  `Text` and call `Static.update()` rather than remounting.
- `collapsed=False` while running, `collapsed=True` after `mark_done()`.
- Records its own start `time.monotonic()` so it can compute elapsed seconds
  for its title.

### `_ToolCall(Collapsible)`

- Title while running: `⠋ {tool_name}({short_args})` — `short_args` is
  `", ".join(f"{k}={v!r}")` with each value truncated to 40 chars.
- Title once result attached: `✓ {tool_name} → {short_result}` (or
  `✗ {tool_name} → error: {short_error}` if the result indicates error). The
  spinner timer is cancelled. `short_result` truncates to 80 chars.
- Body while running: full args block (pretty-printed), plus a placeholder
  "(running…)" that's replaced by the full result on attach.
- Body once result attached: full args block + full result block, both as
  Rich `Text` to defeat markup parsing of bracketed content.
- `collapsed=False` while running, `collapsed=True` after `attach_result()`.
- Detection of error result: best-effort — `True` if the result content is
  a `dict` with `is_error=True`, or a `str` starting with `Error:`. Otherwise
  treated as success.

### Spinner

A single `set_interval(0.08, self._tick)` per running widget. `_tick` advances
an integer index modulo the length of `⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏` and rewrites the
foldable's title with the new glyph. Stopped via `Timer.stop()` on
`mark_done()` / `attach_result()`. We avoid `LoadingIndicator` because it's
block-level and would inflate every row.

### `RichTranscript` outer logic

- `__init__(self, *, agent_id: str, event_bus: EventBus | None = None)`
- On mount: replay history (see below), then subscribe to
  `AgentMessageAppended` and `AgentStateChanged`. Both subscriptions filter
  on `agent_id == self._agent_id`.
- `_current_turn: _TurnContainer | None` tracks the in-flight turn.
- On `AgentMessageAppended`:
  - `role == "user"`: if a current turn exists and is still `running`, force
    `mark_done()` on it (defensive; shouldn't happen given `_send_lock`).
    Open a new `_TurnContainer(user_text=text)` and mount it.
  - `role == "assistant"`: `_current_turn.add_text(text)`.
  - `role == "thinking"`: `_current_turn.add_thinking(text)`.
  - `role == "tool_use"`: `_current_turn.add_tool_call(tool_id, tool_name, text)`.
  - `role == "tool_result"`: `_current_turn.attach_tool_result(tool_id, text)`.
- On `AgentStateChanged`: if `info.state in (DONE, ERROR)` and a current turn
  exists, call `mark_done()` / `mark_error()` and clear `_current_turn`.

### History replay

On mount, before subscribing, read the on-disk transcript and walk entries in
order, using the same dispatch as live events. Heuristic for replay-time
turn boundaries: each `user` entry opens a new turn; if the final entry is
not preceded by a state-changed signal (it isn't, since state isn't
persisted), assume the turn is `done` and call `mark_done()` on it after the
last entry. This gives the desired "all collapsed on remount" behavior.

For old transcripts where `tool_id` is `None`: tool-result attachment falls
back to "attach to most recently mounted `_ToolCall` whose result hasn't been
set yet." If none exists, mount a free-floating `_ToolCall` with the result.

## Styling

In `RichTranscript.DEFAULT_CSS`:

- `_TurnContainer` gets `margin-top: 1` to visually separate turns.
- `.turn-running` has a left-border accent (`border-left: thick $accent`),
  `.turn-done` has none.
- Collapsed `_ThinkingGroup` titles in `$text-muted`.
- Collapsed `_ToolCall` titles: success in `$text-muted`, error in `$error`.
- Final-text `Static` (class `-final`) keeps default text color, with bold
  `claude: ` prefix and no margin so it reads as the natural reply.

The outer `OrchestratorChat` / `AgentTranscript` keep their existing
`border: round …` and `padding: 0 1`.

## Testing

`tests/widgets/test_rich_transcript.py`, run under `pytest-textual`:

- **Turn opening:** publishing `AgentMessageAppended(role="user")` mounts a
  new `_TurnContainer` whose first child renders the user text.
- **Thinking grouping:** two `thinking` events back-to-back land in one
  `_ThinkingGroup`; a `thinking` after a `tool_use` opens a *new* group.
- **Tool pairing by id:** `tool_use(id="abc", name="X")` mounts a `_ToolCall`;
  matching `tool_result(tool_id="abc")` attaches to it and the foldable
  collapses with `✓` in the title.
- **Markup safety:** tool args containing `[type=int_parsing]` render
  literally and don't raise a Rich markup parse error.
- **Turn completion:** after `AgentStateChanged → DONE`, all
  `_ThinkingGroup` and `_ToolCall` widgets in the current turn are collapsed
  and have their spinner timers stopped; `_ThinkingGroup` titles read
  `Thought for {n}s`.
- **History replay on remount:** mounting a `RichTranscript` with a
  preloaded transcript renders each prior turn as collapsed.
- **Old-transcript fallback:** entries lacking `tool_id` still pair correctly
  using the most-recent-pending heuristic.
- **Outer-widget API preserved:** existing `OrchestratorChat` /
  `AgentTranscript` tests (input submit, message-bus interactions, ID
  presence) continue to pass without modification.

## Risk and rollback

- Adding optional fields to `AgentMessageAppended` and `TranscriptEntry` is
  backwards-compatible. Removing the `OrchestratorReply` re-publish path is
  the only behavior-changing edit upstream of the widget; if it causes an
  unexpected regression, restoring those few lines is trivial.
- The new widget is mounted by name through the existing widget registry, so
  rolling back means: (a) revert `widgets/__init__.py` and `widgets/`
  changes, (b) revert `OrchestratorSession._on_message_appended`,
  (c) revert event-schema additions. No data-store migrations.

## Out of scope (deferred)

- Streaming partial-block text.
- Global expand-all / collapse-all keybindings.
- Persisting per-foldable expand state across remounts.
- Visual indicator for tool-result truncation (the existing 240-char
  truncation in `OrchestratorSession` is removed — `_ToolCall` shows the full
  result inside the foldable).
