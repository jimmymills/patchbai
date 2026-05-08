# Orchestrator Session Resume + /reset and /resume — Design

## Goal

When the user reloads `patchfeld`, the orchestrator agent should remember the
previous conversation. Today the chat panel *visually* shows past turns
(replayed from `orchestrator.jsonl`), but the underlying Claude Agent SDK
session is a brand-new one — the agent has no memory of any of it.

In addition to fixing that bug, expose two slash-commands the user can type
into the orchestrator input:

- `/reset` — start a fresh orchestrator session, keep the old transcript
  on disk so it remains resumable later.
- `/resume` — open a picker of past orchestrator sessions for this project
  and re-attach to the chosen one. `/resume <session_id>` skips the picker.

Default launch behavior: auto-resume the most recent session for this
project. `/reset` is the explicit way to start fresh.

## Scope

**In scope:**

- The orchestrator session only.
- Persisting and resuming SDK `session_id` for the orchestrator.
- Per-session transcript files keyed by SDK `session_id`.
- A picker modal (`ResumeScreen`) for past sessions.
- One-time migration of any pre-existing legacy `orchestrator.jsonl`.

**Out of scope:**

- Child-agent resume. Children spawned via `spawn_agent` remain ephemeral
  per launch as today. The hook design here does not preclude adopting the
  same mechanism for child `AgentSession`s in a follow-up.
- A cross-project session list. The index is per-cwd, like every other
  state file under `.patchfeld/`.
- Editing or deleting past sessions from the picker (read-only).
- A standalone `/sessions` listing command — the picker covers it.
- Token/cost backfill for legacy sessions (we never captured those).
- SDK `fork_session` support.
- New keybindings for `/reset` or `/resume`. Typing the command into the
  existing chat input is the entire UX.

## Constraints from earlier brainstorming decisions

- **Scope:** orchestrator only. Children later.
- **`/resume` with no arg:** opens a picker of past sessions.
- **`/reset` history handling:** start a fresh SDK session, keep the old
  transcript on disk (rotated by session_id). Old session remains
  resumable via `/resume`.
- **Launch default:** auto-resume the most recent session. `/reset` is
  explicit.

## Architecture

### Data model

#### Per-cwd index — `.patchfeld/orchestrator_sessions.json`

```json
[
  {
    "session_id": "9c4f1a8e-…",
    "transcript_path": ".patchfeld/transcripts/orchestrator.9c4f1a8e-….jsonl",
    "started_at": 1714939200.12,
    "last_activity": 1714942800.55,
    "first_user_message": "Help me design the resume feature",
    "num_turns": 14,
    "tokens_in": 12480,
    "tokens_out": 3120,
    "cost": 0.082,
    "legacy": false
  }
]
```

Written atomically via `write_json_atomic`. "Most recent" = entry with
the largest `last_activity`.

`legacy=true` marks an entry whose `session_id` is a synthetic
`legacy-<timestamp>` string (from migration). Legacy entries appear in
the picker but cannot be passed to the SDK as `resume=…`; selecting one
falls through to `/reset` semantics with a user-visible notice.

#### Per-session transcript file

Each session's transcript lives at:

```
.patchfeld/transcripts/orchestrator.<session_id>.jsonl
```

This replaces today's single `.patchfeld/transcripts/orchestrator.jsonl`.
The active session's path is resolved through the index, never hard-coded.

#### Migration (one-time, on first launch after this lands)

If `.patchfeld/transcripts/orchestrator.jsonl` exists and no
`orchestrator_sessions.json` is present:

1. Generate `legacy_id = f"legacy-{int(started_at_or_mtime)}"`.
2. Rename `orchestrator.jsonl` → `orchestrator.<legacy_id>.jsonl`.
3. Insert one `legacy=true` entry into the index. `last_activity` =
   file mtime; counts/cost left at zero.

The legacy file is preserved on disk and listed in the picker so the
user can see "yes, my old session is still there." Selecting it does
NOT replay it as live transcript; instead it starts a fresh SDK session
(see `/resume legacy entry` under Control flow) with a notification
explaining why. The legacy JSONL stays on disk for manual inspection.

### Module additions

| Path | Purpose |
| --- | --- |
| `patchfeld/persistence/orchestrator_sessions.py` | `OrchestratorSessionEntry` dataclass + `OrchestratorSessionsIndex(cwd)` with `list()`, `upsert(entry)`, `most_recent()`, `migrate_legacy_if_needed()`. |
| `patchfeld/widgets/resume_screen.py` | `ResumeScreen` modal — `DataTable` of entries, Enter selects, Esc cancels. Returns `session_id` to the caller. |
| `patchfeld/persistence/paths.py` | New helper `orchestrator_session_transcript_path(cwd, session_id) -> Path` returning `<cwd>/.patchfeld/transcripts/orchestrator.<session_id>.jsonl`. |
| `patchfeld/persistence/transcript_store.py` | `AgentTranscript.__init__` gains an optional `path: Path \| None = None` override. When provided, it bypasses the `agent_id`-derived path. `agent_id` is retained only for diagnostics/logging in this case. |

### Modified modules

- `patchfeld/agents/session.py`
  - New optional ctor arg `on_session_id: Callable[[str], None] \| None`.
  - In `_handle_message` for `ResultMessage`: capture `msg.session_id`,
    store on `self._session_id`, fire `on_session_id` exactly once.
  - Public read-only `session_id` property.
- `patchfeld/orchestrator/session.py`
  - `start()` now consults the index, decides `resume=<id>` vs new
    `session_id=<uuid>`, points the inner `AgentTranscript` at the
    appropriate per-session JSONL, and registers the session in the
    index after the first `ResultMessage` confirms.
  - `_on_user_message` adds a slash-command parser before delegating
    to `_inner.queue_send`. Recognized: `/reset`, `/resume`,
    `/resume <id>`. Unknown `/foo` falls through to the agent as text
    (so `/help` etc. work as before — the agent's own slash handling
    isn't intercepted).
  - New methods `reset()` and `resume(session_id)` plus a private
    `_swap_inner(...)` helper guarded by `asyncio.Lock`.
  - On every send/result, refresh `last_activity` / counts in the index.
- `patchfeld/events.py`
  - `OrchestratorSessionSwitched(session_id: str, transcript_path: str)`
  - `OpenResumePicker()`
- `patchfeld/app.py`
  - Subscribe to `OpenResumePicker` and `push_screen(ResumeScreen)`,
    on dismiss call `orchestrator.resume(<picked_id>)` (or no-op).
  - Update `action_show_help` to mention `/reset` and `/resume`.
- `patchfeld/widgets/rich_transcript.py`
  - New `replace_source(transcript_path: Path)` that clears the
    `VerticalScroll`, drops the current turn, and replays from the
    new path. Live event filtering still keys off `agent_id`, which
    stays `"orchestrator"`. Subscribe to `OrchestratorSessionSwitched`
    to invoke `replace_source(event.transcript_path)`.
  - `__init__` gains an optional `transcript_path: Path | None = None`
    override. When provided, replay reads from that path instead of
    deriving one from `agent_id`. `OrchestratorChat` resolves the
    active path through the orchestrator and passes it down.
- `patchfeld/widgets/orchestrator_chat.py`
  - Update placeholder to hint at `/reset` and `/resume`.

### Control flow

#### Launch

1. `PatchfeldApp.on_mount` calls `orchestrator.start()` as today.
2. `OrchestratorSession.start()`:
   - Calls `OrchestratorSessionsIndex.migrate_legacy_if_needed()`.
   - Loads the index. If `most_recent()` exists with `legacy=False`,
     resolve its transcript path and build options with
     `resume=entry.session_id`. Otherwise mint a new `uuid4` for
     `session_id` and use a fresh transcript path.
   - Build `AgentTranscript(cwd=cwd, agent_id="orchestrator", path=
     orchestrator_session_transcript_path(cwd, session_id))`. The
     `agent_id` stays `"orchestrator"` so `AgentMessageAppended` events
     and downstream subscribers (`RichTranscript`, the chat panel,
     `OrchestratorSession._on_message_appended`) keep filtering on the
     stable id. The per-session JSONL filename is decoupled via the
     new `path` override on `AgentTranscript`.
   - Construct the inner `AgentSession` with
     `on_session_id=self._on_session_id_observed`.
   - `await self._inner.start(options=ClaudeAgentOptions(... resume=…|session_id=…))`.
3. First `ResultMessage` triggers `on_session_id`, which upserts the
   index entry. For `resume=` flows the observed id should match the
   passed id (assert + log a warning if not).
4. `RichTranscript` for the orchestrator panel keeps
   `agent_id="orchestrator"` for live-event filtering. It now also
   accepts a `transcript_path` override. `OrchestratorChat.compose`
   resolves the active path via the app's orchestrator
   (`app.orchestrator.active_transcript_path`) and passes it through.
   Subsequent `OrchestratorSessionSwitched` events drive the in-place
   swap via `replace_source(event.transcript_path)`.

#### `/reset`

1. `OrchestratorSession._on_user_message` matches `^/reset(\s|$)`.
2. `await self._reset()`:
   - `await self._inner.interrupt()` (no-op when idle).
   - `await self._inner.stop()`.
   - Mint new `session_id = uuid4().hex`.
   - Build a fresh `AgentTranscript` and inner `AgentSession`,
     `start()` with `session_id=<new>` (no `resume`).
   - Index gets a new entry on the next `ResultMessage`.
   - Publish `OrchestratorSessionSwitched(new_session_id, new_path)`.
3. `RichTranscript.replace_source` clears the scroll, replays from the
   new (empty) path. The next user prompt opens the first turn.

#### `/resume` (no arg)

1. Parser publishes `OpenResumePicker()` and returns — does NOT send
   the literal `/resume` to the agent.
2. `PatchfeldApp` `push_screen(ResumeScreen, on_picked)`.
3. `ResumeScreen` reads from `OrchestratorSessionsIndex.list()`,
   sorts by `last_activity` desc, displays up to 50 rows. Columns:
   when (relative), first_user_message (truncated to 60 chars),
   num_turns, tokens (in/out), session_id (short).
4. `on_picked(session_id | None)`. None → no-op. A non-legacy id →
   `await orchestrator.resume(session_id)`. A legacy id → notify
   "This session predates SDK resume support; starting a fresh
   session" then `await orchestrator.reset()`.

#### `/resume <session_id>`

1. Parser matches `^/resume\s+(\S+)$`. Looks up the entry; unknown id
   → notify "no such session" and no-op. Legacy → same fallback as
   above. Real id → `await orchestrator.resume(session_id)`.

#### `orchestrator.resume(session_id)`

Same swap as `/reset`, with three differences:

- Options use `resume=session_id` (no fresh `session_id=` field).
- Transcript path is the existing per-session JSONL (replay shows
  past turns again).
- If the SDK rejects the resume (caught around `_inner.start()`),
  surface a notification and fall back to the fresh-session path
  while leaving the old entry in the index (still listed in the
  picker; the SDK CLI just lost the underlying conversation state).

### Concurrency & failure handling

- `OrchestratorSession._switching_lock = asyncio.Lock()` wraps
  `_swap_inner`. Two rapid `/reset` calls serialize.
- `interrupt()` already exists for cancelling an in-flight turn; the
  swap path uses it before `stop()`.
- Index corruption: `OrchestratorSessionsIndex.list()` returns `[]`
  and logs a warning; `start()` then proceeds as a fresh launch.
- `on_session_id` mismatch (passed `resume=A`, observed `B`): log
  warning, prefer the observed id (it's what the SDK actually
  attached us to), and update the in-memory state and index pointer
  before proceeding.
- Migration is idempotent: if `orchestrator_sessions.json` exists or
  there's no legacy `orchestrator.jsonl`, it does nothing.

## UI surface

### `ResumeScreen`

```
┌── Resume orchestrator session ──────────────────────────────────┐
│ when      first message                       turns  tokens   id │
│ 12m ago   Help me design the resume feature      14  12k/3k   9c4f1a8e │
│ 3h ago    Add /reset and /resume commands         7   4k/900  3b110f2c │
│ 2d ago    (legacy session — fresh start)          —      —    legacy-… │
└─ enter: resume · esc: cancel ──────────────────────────────────┘
```

`DataTable` with `cursor_type="row"`. Enter dismisses with
`session_id`; Esc dismisses with `None`. Mirrors the existing
`LayoutSwitcherScreen` pattern.

### Help text

`action_show_help` adds: `/reset new session · /resume past session`.

### Input placeholder

Orchestrator input: `Message orchestrator… (/reset, /resume, ctrl+c interrupt)`.

## Events

```python
@dataclass(frozen=True)
class OrchestratorSessionSwitched:
    session_id: str
    transcript_path: str

@dataclass(frozen=True)
class OpenResumePicker:
    pass
```

Both flow over the existing `EventBus`. `OpenResumePicker` is
published by the orchestrator and consumed by the app; the app
doesn't need to talk back into the orchestrator through events
because it already holds a direct reference (`self.orchestrator`).

## Testing

TDD-first, with `FakeSDKAdapter` (already in
`patchfeld/agents/fake_sdk_adapter.py`). Concrete coverage:

1. **`OrchestratorSessionsIndex` round-trip** — write, read,
   atomic-write semantics, corrupt-file → empty list with WARN.
2. **Legacy migration** — pre-existing `orchestrator.jsonl` with no
   index → file renamed to `orchestrator.legacy-<ts>.jsonl`, one
   `legacy=true` entry inserted, idempotent on second call.
3. **`AgentSession.session_id`** — fake `ResultMessage` with
   `session_id="abc"` ⇒ property returns `"abc"`, `on_session_id`
   callback invoked exactly once across multiple `ResultMessage`s.
4. **Launch with empty index** — adapter's recorded options carry a
   freshly-minted `session_id=<uuid>` and no `resume`.
5. **Launch with prior session** — adapter's recorded options carry
   `resume=<prior_id>`; index `last_activity` updates after the
   first `ResultMessage`.
6. **End-to-end resume across "restarts"** — start session A, send
   one turn, stop. Construct a fresh `OrchestratorSession` with
   the same cwd. Adapter records `resume=A` on `start()`.
7. **`/reset`** — sending `"/reset"` doesn't reach the SDK as a
   prompt; inner session swapped; new JSONL created, old JSONL
   untouched on disk; `OrchestratorSessionSwitched` published with
   the new id and path.
8. **`/resume <id>`** — explicit id swap; adapter sees `resume=<id>`;
   widget receives `OrchestratorSessionSwitched`.
9. **`/resume <bad_id>`** — no swap; user-visible notification.
10. **`/resume <legacy_id>`** — falls through to `/reset` semantics
    with notification text.
11. **Concurrent `/reset` calls** — two in-flight calls serialize via
    `_switching_lock`; second observes the first's swap.
12. **SDK rejects `resume=`** — `FakeSDKAdapter.start` raises on
    `resume=` for one configured id; orchestrator falls back to a
    fresh session, notification surfaced, prior index entry preserved.
13. **`RichTranscript.replace_source`** — Pilot-driven widget test:
    send fake messages on bus A, fire `OrchestratorSessionSwitched`,
    verify scroll cleared and replays from new path; new
    `AgentMessageAppended` events for the new id render.
14. **`ResumeScreen`** — index with three entries renders three rows,
    Enter on row 2 dismisses with that session_id, Esc dismisses
    with `None`.
15. **Slash-command parser** — `/help`, `/foo` (unknown) fall
    through to the agent unchanged; only `/reset` and `/resume`
    are intercepted.

## Risks

- The Claude Agent SDK CLI process owns the actual conversation
  state for `resume=`. If the user `rm -rf`s the SDK's local cache,
  resumes will fail. We surface the failure cleanly; we don't try to
  reconstruct from the JSONL (replay-as-context was rejected in
  brainstorming).
- Per-session JSONL filenames change. Anyone tailing
  `orchestrator.jsonl` directly (humans, scripts) needs to switch
  to the index. There are no in-tree consumers of the literal
  filename outside the persistence layer, so this is contained.
- Per-session JSONL filenames are derived from `session_id`, not
  from `agent_id`. `AgentSession.info.id` stays `"orchestrator"` so
  every existing `event.agent_id == "orchestrator"` filter keeps
  working unchanged. The transcript filename is supplied via a new
  `path` override on `AgentTranscript`. Spelled out in the
  implementation plan.
