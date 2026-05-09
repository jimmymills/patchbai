# Patchfeld Performance Plan

> Status: investigation complete, no code changes yet. Each section below is sized to be its own branch + PR with manual testing.
>
> Author: jimmy.mills@buoyhealth.com — 2026-05-09
>
> Branch of investigation: `worktree-perf-investigation`

## Context

User report: the app feels slow when interacting with **agents and the orchestrator**. Goal of this plan is **behavior-preserving** wins only — no UX/feature changes. Findings come from a triangulated audit of (a) the agent I/O hot path, (b) the orchestrator session/tool layer, and (c) the Textual reactivity / rendering layer.

Three big clusters drive the felt slowness:

1. **Synchronous disk I/O on the asyncio event loop** — config TOML, orchestrator session index, transcript JSONL, notebook scratchpad, log tails. Many of these fire per-turn or per-chunk.
2. **Per-chunk widget churn** — Pyte terminal full re-renders, Collapsible title rebuilds at 12.5 Hz × 3 spinners, modal push/dismiss thrash.
3. **Event bus N-way fanout with per-subscriber filtering** — every `AgentMessageAppended` is delivered to every transcript widget, which then filters by `agent_id`.

Sections are ranked by likely user-perceived impact. Each is independent — they can land in any order, on separate branches.

---

## Tier 1 — high impact, low blast radius

### 1. Cache `OrchestratorSessionsIndex` in memory

- **Symptom.** Every assistant message in the orchestrator triggers `_refresh_session_summary()` (`patchfeld/orchestrator/session.py:832-853`), which calls `OrchestratorSessionsIndex.get()`. `get()` calls `list()`, which does a full `Path.read_text()` + `json.loads()` of `orchestrator_sessions.json`, then a linear scan (`patchfeld/persistence/orchestrator_sessions.py:40-80`). `upsert()` calls `list()` again. Two disk hits per turn, every turn — all on the event loop.
- **Fix.** Add an in-memory `dict[id → SessionRecord]` to `OrchestratorSessionsIndex`. Load lazily on first access; keep it authoritative; persist on `upsert`/`set_title`/`delete`. No external caller change required.
- **Risks.** Multi-process writers (another patchfeld instance) could write under us. Today the index is per-process and we already race on plain file writes, so this is not a regression. Worth a comment noting the assumption.
- **Manual test.** (a) Launch app with an existing session, send 10 turns to the orchestrator, verify all show up in `resume`. (b) Open two patchfeld instances simultaneously — confirm at least one's view of the index updates after restart (documenting current behavior). (c) Use `dtruss`/`fs_usage` (or `strace` Linux) on `orchestrator_sessions.json` and confirm reads drop to ~1 per session start.
- **Effort.** S–M.
- **Suggested branch.** `perf/orch-index-cache`.

### 2. Cache `ConfigStore.load()` per orchestrator session

- **Symptom.** Six orchestrator tool handlers call `config_store.load()` (`patchfeld/orchestrator/tools.py:224, 236, 251, 264, 285` and one more), and each load does `tomllib.loads(path.read_text())` (`patchfeld/config.py:73-83`). A turn that invokes `bind_key` then `list_bindings` re-reads + re-parses the same file twice.
- **Fix.** Hold a cached `Config` on the `OrchestratorSession` (or a thin wrapper). Invalidate on the same-process writers (`bind_key`, `unbind_key`, `set_config`). Cross-process invalidation is not a goal.
- **Risks.** Stale config if user edits `~/.config/patchfeld/config.toml` by hand mid-session. Acceptable; document or mtime-check.
- **Manual test.** (a) Bind/unbind a key from the orchestrator, confirm `list_bindings` reflects it. (b) Externally edit the TOML mid-session, confirm whatever behavior we documented (probably "needs restart" or "checks mtime"). (c) Add a debug log in `Config.load`, run a normal turn, confirm load count drops.
- **Effort.** S.
- **Suggested branch.** `perf/orch-config-cache`.

### 3. Batch / async `transcript_store.append`

- **Symptom.** Every `AgentMessageAppended` runs `transcript_store.append`, which opens the file in append mode, `json.dumps`, writes, closes (`patchfeld/persistence/transcript_store.py:38-41`). Streaming text from the SDK comes in many small chunks (thinking blocks, tool deltas, assistant tokens) → one disk write per chunk on the event loop.
- **Fix options** (pick one in the PR):
  - (a) Keep a long-lived file handle per agent transcript and `flush()` on a short timer (e.g. 250 ms) or on agent idle.
  - (b) Push appends onto an `asyncio.Queue` consumed by a single background writer task per agent.
  - (c) Use `loop.run_in_executor` for the write.
- **Recommendation:** (a) is simplest and matches existing single-process assumptions.
- **Risks.** Crash-loss window of up to one flush interval. Acceptable for transcripts (they are derivable from the SDK history) but call this out in the PR.
- **Manual test.** (a) Send a large streaming response (e.g. ask agent to "list 200 numbers"), confirm transcript file grows progressively and final content is identical to the SDK history. (b) `kill -9` patchfeld mid-stream; confirm at most one flush interval of trailing data is missing. (c) Resume the agent — confirm replay works.
- **Effort.** M.
- **Suggested branch.** `perf/transcript-store-batched`.

### 4. Unified spinner ticker

- **Symptom.** Three different `Collapsible` subclasses each call `set_interval(0.08, …)` in `patchfeld/widgets/rich_transcript.py:62, 140, 208`. With a typical turn that has 1 thinking block + a couple of tool calls there are 3+ live timers at 12.5 Hz, each rebuilding a title string and reassigning `self.title`, which re-renders the Collapsible title node even when the string is identical to the previous tick.
- **Fix.** (a) Replace the per-widget `set_interval` with a single app-level ticker that pushes a phase index into a shared module-level slot; running widgets read it on-demand (or are notified by a single message). (b) Memoize: only assign `self.title` if the rendered string differs from the previous one.
- **Risks.** Timing of when widgets stop animating on completion needs care — currently each widget cancels its own interval. The new design needs a clean way to register/unregister participants.
- **Manual test.** Visual: spinner still spins smoothly during streaming, stops crisply on done/error. Quick smoke: trigger 3+ concurrent tool calls and confirm CPU usage drops vs. baseline (`top -pid $(pgrep -f patchfeld)`).
- **Effort.** S.
- **Suggested branch.** `perf/spinner-unify`.

---

## Tier 2 — meaningful, more invasive

### 5. EventBus subscription keyed by `agent_id`

- **Symptom.** `EventBus.publish` (`patchfeld/events.py:247-260`) iterates every subscriber for a given event type. Every mounted `AgentTranscript` / `RichTranscript` subscribes to `AgentMessageAppended`, `PermissionRequested`, etc., and the first thing each handler does is `if event.agent_id != self._agent_id: return`. With N agents mounted this is N× wasted dispatch per event.
- **Fix.** Extend `subscribe` so callers can pass an optional `agent_id` filter: `bus.subscribe(AgentMessageAppended, handler, agent_id=self._agent_id)`. Internally, store a second index keyed by `(event_type, agent_id)`. Publishers dispatch to type-wide subscribers + type+agent-id subscribers. No public-API break.
- **Risks.** Need to verify all current event types either have no `agent_id`, or the handler always filtered — otherwise the change could mask events for some subscribers.
- **Manual test.** With 3+ agents running concurrently, confirm each agent's transcript only shows its own output. Tail log at debug level to count handler invocations per event.
- **Effort.** M.
- **Suggested branch.** `perf/eventbus-keyed-sub`.

### 6. Lazy / windowed transcript replay on mount

- **Symptom.** `RichTranscript.on_mount` calls `store.read_all()` which reads the entire JSONL transcript synchronously and replays each entry through `_dispatch_entry`, mounting Collapsible widgets for every tool call / thinking group (`patchfeld/widgets/rich_transcript.py:466-486`). Opening a tab with a long history stalls the UI.
- **Fix.** (a) Read the file from the tail backwards, mount the last N entries (e.g. 100), then incrementally page in older entries on scroll-up via a worker. (b) Or: `await asyncio.sleep(0)` between mounts so the loop can yield.
- **Risks.** Existing scroll-position semantics might shift; needs UX-preserving handling. Anchors / "jump to top" must still work.
- **Manual test.** Generate a large transcript (e.g. resume an agent that has 1k+ entries), confirm the tab opens snappily and history is fully reachable by scrolling.
- **Effort.** M.
- **Suggested branch.** `perf/transcript-lazy-replay`.

### 7. Persistent `PermissionModal`

- **Symptom.** `PermissionModal` is a `ModalScreen` pushed/dismissed on every permission request (`patchfeld/widgets/permission_modal.py:20-94`). Each push remounts the entire widget tree.
- **Fix.** Mount the modal once on app startup (hidden), update its content (`Static.update`, `Label.update`) on each request, toggle visibility via class.
- **Risks.** Screen-stack semantics (focus return, back-stack) may behave differently. Worth confirming Textual handles a long-lived hidden ModalScreen cleanly; if not, use a `Container` with absolute positioning instead.
- **Manual test.** Spam permission prompts (run an agent that triggers ~10 in quick succession) — confirm queue is preserved and visual transition is at least as smooth.
- **Effort.** M.
- **Suggested branch.** `perf/permission-modal-persistent`.

#### 7a. Deferred 2026-05-09 — exploratory notes

After reading `patchfeld/app.py:1238-1266` and `patchfeld/widgets/permission_modal.py` end-to-end, the original framing is slightly off and the fix is more invasive than its tier suggests. Recording what's known so a future attempt doesn't repeat the audit.

- **Re-framing the cost.** The remount is **per-burst**, not per-request. `app._on_permission_requested` latches `_permission_modal_open` and pushes one modal; subsequent requests while it's up are queued *inside* the modal's own `PermissionRequested` subscription (`permission_modal.py:90-104`). The remount only happens when (a) the queue empties and the modal `dismiss()`es, then (b) a new request arrives. So the user-visible flicker is "modal closes briefly, opens again" between bursts — not on every prompt.
- **Three approaches considered.**
  1. **Install + cache the `ModalScreen`.** Use Textual's `SCREENS = {...}` install pattern, push the cached instance instead of constructing fresh. Smallest test diff, but Textual's screen lifecycle still re-runs `compose` and `on_mount` on each `push_screen`, so the perf win is partial. Worth measuring before committing.
  2. **Convert to a non-screen overlay `Container`.** Mount once on app boot inside the main screen, toggle visibility via class. Biggest perf win and cleanest mental model, but breaks the screen-stack focus trap and the existing tests assume `app.screen is PermissionModal` (see `tests/test_widget_permission_modal.py:164-168` and `tests/test_app_smoke_permission_modal.py:39, 59, 80, 118, 131`). Substantial test-API churn.
  3. **Defer.** The hot path inside a burst already queues without remount. The remaining cost is one mount per burst, which on a Mac is on the order of single-digit ms — only painful if permission bursts arrive every few hundred ms.
- **Recommendation when revisited.** Try (1) first — it's the smallest delta and quickest to evaluate. Drop a `time.perf_counter` around `compose`/`on_mount` and run the manual spam test (10 quick prompts). If (1) doesn't move the needle, commit to (2) and budget for the test rewrite (~7 tests need to switch from `app.screen` to a `query_one(PermissionModal)` shape).
- **Surprising-finding hook.** The `_permission_modal_open` latch in `app.py` is a good clue this code already had a "don't push twice" instinct — the cached-instance path is its natural extension.

### 8. Debounce notebook saves

- **Symptom.** `Notebook` writes to disk on every `TextArea.Changed` (`patchfeld/widgets/notebook.py:36-38`).
- **Fix.** Debounce ~500 ms; flush on blur, on app shutdown, and on a periodic safety timer (e.g. every 5 s).
- **Risks.** Crash-loss of last unsaved keystrokes. Document; ensure shutdown flush exists.
- **Manual test.** Type rapidly into the notebook; confirm only one write happens after a typing pause. `kill -INT patchfeld`; confirm contents persist.
- **Effort.** S.
- **Suggested branch.** `perf/notebook-debounce`.

---

## Tier 3 — small wins (could batch into one polish PR)

### 9. Skip pyte terminal refresh when screen is unchanged

- **Symptom.** `widgets/terminal.py:182-211` calls `_refresh()` on every PTY chunk; `_refresh` rebuilds Rich Text from every cell of an 80×24 grid even if the chunk produced no visible change (e.g. unicode lead bytes, escape continuations).
- **Fix.** Cache a hash of the rendered cell grid; bail if identical. Or use pyte's `dirty` lines if exposed.
- **Manual test.** Stream a long log to a terminal-backed widget; confirm output is identical and CPU drops.
- **Effort.** S.
- **Suggested branch.** `perf/terminal-dirty-skip`.

### 10. `_TurnContainer.mark_done/error` query thrash

- **Symptom.** `patchfeld/widgets/rich_transcript.py:391-405` does multiple `self.query(...)` traversals + `add_class`/`remove_class` chains on every turn end.
- **Fix.** Track child references at construction time; use a single state class that toggles via CSS variants.
- **Manual test.** Turn-end visual transition is identical.
- **Effort.** S.
- **Suggested branch.** `perf/turn-mark-done`.

### 11. Drop `indent=2` from orchestrator tool result JSON

- **Symptom.** `patchfeld/orchestrator/tools.py` has ~10 `json.dumps(..., indent=2)` calls. The model doesn't need indentation; pretty-printing costs CPU and tokens.
- **Fix.** Drop `indent=2`. Only pretty-print when explicitly intended for human display.
- **Risks.** Any caller that parses the text expecting line-level structure (unlikely; should be JSON-loaded). Skim all use sites.
- **Manual test.** Run each tool through the orchestrator, confirm functional output identical.
- **Effort.** S.
- **Suggested branch.** `perf/orch-tool-json-compact`.

### 12. `LogTail` move to worker

- **Symptom.** `patchfeld/widgets/log_tail.py:44` runs sync `stat()` + `read()` every 250 ms on the event loop. Multiple LogTails compound it.
- **Fix.** `run_worker(..., exclusive=True)` like `SystemUsage` already does, or use `aiofiles`.
- **Manual test.** Multi-LogTail view; confirm no event loop hitches when tailing a busy log.
- **Effort.** S.
- **Suggested branch.** `perf/log-tail-worker`.

### 13. Incremental stats aggregation

- **Symptom.** `app.py:1206-1236` (`_on_stats_changed`) re-aggregates token totals across all agents on every `AgentTokensTouched` event.
- **Fix.** Maintain a running `(tokens_in, tokens_out, cost)` total updated by deltas in the event payload; full re-scan only on add/remove of an agent.
- **Risks.** Drift if a single event is dropped; periodic full re-scan as safety net.
- **Manual test.** Run several agents simultaneously, compare displayed totals against a manual sum after a long session.
- **Effort.** S.
- **Suggested branch.** `perf/stats-incremental`.

---

## Cross-cutting: instrumentation first?

Before landing any of the above, it is worth a one-shot **measurement** PR so we can put numbers on each fix:

- A `--profile` startup flag that wires `tracemalloc` + `cProfile` (or [py-spy](https://github.com/benfred/py-spy) externally — no code change needed).
- A lightweight in-app "perf overlay" toggle (key chord) that prints, once per second: event-loop tick count, time spent in `_refresh()`, count of disk writes since last sample, EventBus dispatches/sec.

This is **optional** — the Tier 1 items are mechanical enough that we can ship without instrumentation and rely on subjective feel + manual confirmations. But if we want hard before/after numbers in PR descriptions, build this first.

- **Suggested branch.** `perf/instrumentation`.

---

## Out of scope (called out, not in this plan)

- Behavior changes (UX trims, feature simplifications) — explicitly excluded by the brief.
- Switching off Textual's diff/render strategy or upgrading Textual itself.
- Multi-process coordination on shared JSON files (still racy after Tier 1, but no worse than today).
- `claude-agent-sdk`-side changes — outside this repo.

## Open questions

1. Do we want a measurable-improvement PR (instrumentation) before any fix lands, or are subjective-improvement PRs acceptable?
2. For #3 (transcript writes), preference between persistent file handle vs. background writer task vs. executor?
3. For #5 (event bus keyed sub), is breaking subscribers that *rely* on receiving cross-agent events acceptable? (Audit needed before implementation.)
4. Do we land Tier 3 as 5 separate PRs or one batched "polish" PR?
