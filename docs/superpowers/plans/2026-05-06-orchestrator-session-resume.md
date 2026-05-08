# Orchestrator Session Resume + /reset and /resume — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the orchestrator agent remember prior conversations across app
reloads (auto-resume), and add `/reset` and `/resume` slash-commands to start a
new session or re-attach to a past one.

**Architecture:** Capture the SDK `session_id` from `ResultMessage`, persist a
small per-cwd index of past orchestrator sessions, pass `resume=<id>` in
`ClaudeAgentOptions` on launch, and rotate the orchestrator's transcript JSONL
to one file per session. `/reset` and `/resume` are intercepted in
`OrchestratorSession._on_user_message` before reaching the SDK; they swap the
inner `AgentSession` under an `asyncio.Lock` and publish
`OrchestratorSessionSwitched` so `RichTranscript` can replay from the new path.

**Tech Stack:** Python 3.12, `claude_agent_sdk`, Textual, pytest +
`pytest-asyncio`, existing `EventBus` pub/sub, existing `FakeSDKAdapter` for tests.

**Spec:** `docs/superpowers/specs/2026-05-06-orchestrator-session-resume-design.md`

---

## File Structure

**New files:**

- `patchfeld/persistence/orchestrator_sessions.py` — `OrchestratorSessionEntry`
  dataclass + `OrchestratorSessionsIndex` with `list()`, `upsert()`,
  `most_recent()`, `get(session_id)`, `migrate_legacy_if_needed()`.
- `patchfeld/widgets/resume_screen.py` — `ResumeScreen` modal listing past
  sessions; returns picked `session_id` (or `None`).
- `tests/test_orchestrator_sessions_index.py` — round-trip + migration tests.
- `tests/test_orchestrator_session_resume.py` — `OrchestratorSession.start`
  consults the index; `/reset` and `/resume` end-to-end with `FakeSDKAdapter`.
- `tests/test_rich_transcript_replace_source.py` — Pilot test that the widget
  re-renders from a new path on `OrchestratorSessionSwitched`.
- `tests/test_resume_screen.py` — modal renders one row per index entry,
  Enter/Esc dismiss correctly.

**Modified files:**

- `patchfeld/persistence/paths.py` — add `orchestrator_session_transcript_path`.
- `patchfeld/persistence/transcript_store.py` — `AgentTranscript.__init__`
  accepts an optional `path` override.
- `patchfeld/agents/session.py` — add `session_id` property and optional
  `on_session_id` callback; capture `ResultMessage.session_id`.
- `patchfeld/orchestrator/session.py` — index integration on `start`, slash-command
  parser, `reset()` and `resume()` methods, switching lock,
  `active_transcript_path` property.
- `patchfeld/events.py` — `OrchestratorSessionSwitched`, `OpenResumePicker`.
- `patchfeld/app.py` — subscribe to `OpenResumePicker`; update help text.
- `patchfeld/widgets/rich_transcript.py` — accept a `transcript_path` ctor arg;
  `replace_source(path)` method; subscribe to `OrchestratorSessionSwitched`.
- `patchfeld/widgets/orchestrator_chat.py` — pass active path through; update
  input placeholder.

---

## Task 1: `AgentTranscript` accepts an optional `path` override

**Files:**
- Modify: `patchfeld/persistence/transcript_store.py`
- Test: `tests/test_transcript_store.py`

The orchestrator needs to write to per-session JSONLs (`orchestrator.<session_id>.jsonl`)
without changing `AgentSession.info.id`, which downstream code filters on. The
cleanest decoupling is an explicit `path` argument that overrides the
`agent_id`-derived filename.

- [ ] **Step 1: Read the current `AgentTranscript` and the relevant tests**

Run: `cat patchfeld/persistence/transcript_store.py tests/test_transcript_store.py`

Expected: confirms the current ctor signature `(cwd, agent_id)` and the path
helper used internally.

- [ ] **Step 2: Write the failing test for the path override**

Append to `tests/test_transcript_store.py`:

```python
def test_agent_transcript_path_override_uses_explicit_path(tmp_path):
    custom = tmp_path / "custom_dir" / "my_session.jsonl"
    t = AgentTranscript(cwd=tmp_path, agent_id="orchestrator", path=custom)
    t.append(TranscriptEntry(role="user", text="hi"))
    assert custom.exists()
    assert (tmp_path / ".patchfeld" / "transcripts" / "orchestrator.jsonl").exists() is False


def test_agent_transcript_path_override_creates_parents(tmp_path):
    custom = tmp_path / "deep" / "nested" / "x.jsonl"
    t = AgentTranscript(cwd=tmp_path, agent_id="ignored", path=custom)
    t.append(TranscriptEntry(role="user", text="hi"))
    assert custom.exists()


def test_agent_transcript_path_override_reads_back(tmp_path):
    custom = tmp_path / "x.jsonl"
    t = AgentTranscript(cwd=tmp_path, agent_id="ignored", path=custom)
    t.append(TranscriptEntry(role="user", text="hello"))
    t.append(TranscriptEntry(role="assistant", text="hi"))
    out = AgentTranscript(cwd=tmp_path, agent_id="ignored", path=custom).read_all()
    assert [e.text for e in out] == ["hello", "hi"]
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `uv run pytest tests/test_transcript_store.py -v`

Expected: FAIL — `AgentTranscript.__init__` got unexpected kwarg `path`.

- [ ] **Step 4: Implement the override**

Replace the `AgentTranscript.__init__` and update `append` to ensure the parent
of `self._path` exists (the existing `project_transcripts_dir(cwd).mkdir`
doesn't help when the path is outside `.patchfeld/transcripts/`):

```python
class AgentTranscript:
    """Append-only JSONL transcript for one agent."""

    def __init__(
        self,
        cwd: Path,
        agent_id: str,
        *,
        path: Path | None = None,
    ) -> None:
        self._cwd = cwd
        self._agent_id = agent_id
        self._path = Path(path) if path is not None else project_transcript_path(cwd, agent_id)

    def append(self, entry: TranscriptEntry) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(entry)) + "\n")
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `uv run pytest tests/test_transcript_store.py -v`

Expected: PASS for the three new tests; existing tests still pass.

- [ ] **Step 6: Commit**

```bash
git add patchfeld/persistence/transcript_store.py tests/test_transcript_store.py
git commit -m "feat(transcript): AgentTranscript accepts explicit path override"
```

---

## Task 2: New path helper for per-session orchestrator transcripts

**Files:**
- Modify: `patchfeld/persistence/paths.py`
- Test: `tests/test_paths.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_paths.py`:

```python
def test_orchestrator_session_transcript_path_uses_session_id(tmp_path):
    from patchfeld.persistence.paths import orchestrator_session_transcript_path
    p = orchestrator_session_transcript_path(tmp_path, "abc-123")
    assert p == tmp_path / ".patchfeld" / "transcripts" / "orchestrator.abc-123.jsonl"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_paths.py -v`

Expected: FAIL — `cannot import name 'orchestrator_session_transcript_path'`.

- [ ] **Step 3: Add the helper**

Append to `patchfeld/persistence/paths.py`:

```python
def orchestrator_session_transcript_path(cwd: Path, session_id: str) -> Path:
    return project_transcripts_dir(cwd) / f"orchestrator.{session_id}.jsonl"
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_paths.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add patchfeld/persistence/paths.py tests/test_paths.py
git commit -m "feat(paths): orchestrator_session_transcript_path helper"
```

---

## Task 3: `AgentSession` exposes `session_id` and fires `on_session_id`

**Files:**
- Modify: `patchfeld/agents/session.py`
- Test: `tests/test_agent_session.py`

The first `ResultMessage` of a session carries the SDK's `session_id`. We need
to expose it and fire a callback so `OrchestratorSession` can persist it.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_agent_session.py`:

```python
@pytest.mark.asyncio
async def test_session_exposes_sdk_session_id_after_first_result(tmp_path):
    bus = EventBus()
    adapter = FakeSDKAdapter(scripts=[_ok_script()])
    session = AgentSession(
        info=_info(),
        adapter=adapter,
        transcript=AgentTranscript(cwd=tmp_path, agent_id="a1"),
        bus=bus,
    )
    await session.start(options=ClaudeAgentOptions())
    await session.send("hi")
    await session.wait_idle()
    assert session.session_id == "fake-session"


@pytest.mark.asyncio
async def test_session_on_session_id_fires_once(tmp_path):
    bus = EventBus()
    adapter = FakeSDKAdapter(scripts=[_ok_script(), _ok_script()])
    seen: list[str] = []
    session = AgentSession(
        info=_info(),
        adapter=adapter,
        transcript=AgentTranscript(cwd=tmp_path, agent_id="a1"),
        bus=bus,
        on_session_id=seen.append,
    )
    await session.start(options=ClaudeAgentOptions())
    await session.send("hi")
    await session.wait_idle()
    await session.send("again")
    await session.wait_idle()
    assert seen == ["fake-session"]


@pytest.mark.asyncio
async def test_session_id_is_none_before_first_result(tmp_path):
    bus = EventBus()
    adapter = FakeSDKAdapter(scripts=[_ok_script()])
    session = AgentSession(
        info=_info(),
        adapter=adapter,
        transcript=AgentTranscript(cwd=tmp_path, agent_id="a1"),
        bus=bus,
    )
    await session.start(options=ClaudeAgentOptions())
    assert session.session_id is None
```

- [ ] **Step 2: Run tests to verify failure**

Run: `uv run pytest tests/test_agent_session.py -v`

Expected: FAIL — `AgentSession.__init__` got unexpected kwarg `on_session_id`,
no `session_id` attribute.

- [ ] **Step 3: Implement**

Edit `patchfeld/agents/session.py`. Add `on_session_id` to the constructor and
capture the id in `_handle_message`:

```python
class AgentSession:
    """One Claude Agent SDK session: one adapter, one transcript, one state machine."""

    def __init__(
        self,
        *,
        info: AgentInfo,
        adapter: SDKAdapter,
        transcript: AgentTranscript,
        bus: EventBus,
        on_session_id: "Callable[[str], None] | None" = None,
    ) -> None:
        self.info = info
        self._adapter = adapter
        self._transcript = transcript
        self._bus = bus
        self._on_session_id = on_session_id
        self._session_id: str | None = None
        self._stream_task: asyncio.Task | None = None
        self._idle_event = asyncio.Event()
        self._idle_event.set()
        self._send_lock = asyncio.Lock()

    @property
    def session_id(self) -> str | None:
        return self._session_id
```

In `_handle_message`, inside the `ResultMessage` branch, capture once:

```python
        elif isinstance(msg, ResultMessage):
            if self._session_id is None and msg.session_id:
                self._session_id = msg.session_id
                if self._on_session_id is not None:
                    try:
                        self._on_session_id(msg.session_id)
                    except Exception:
                        # Callback errors must not poison the SDK stream.
                        pass
            usage = msg.usage or {}
```

Add `from typing import Callable` to the top-level imports if not already present.

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/test_agent_session.py -v`

Expected: PASS for all three new tests + no regressions.

- [ ] **Step 5: Commit**

```bash
git add patchfeld/agents/session.py tests/test_agent_session.py
git commit -m "feat(agents): expose AgentSession.session_id + on_session_id callback"
```

---

## Task 4: `OrchestratorSessionsIndex` data layer

**Files:**
- Create: `patchfeld/persistence/orchestrator_sessions.py`
- Test: `tests/test_orchestrator_sessions_index.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_orchestrator_sessions_index.py`:

```python
from pathlib import Path

from patchfeld.persistence.orchestrator_sessions import (
    OrchestratorSessionEntry,
    OrchestratorSessionsIndex,
)


def _entry(sid: str = "s1", last: float = 100.0, legacy: bool = False) -> OrchestratorSessionEntry:
    return OrchestratorSessionEntry(
        session_id=sid,
        transcript_path=f".patchfeld/transcripts/orchestrator.{sid}.jsonl",
        started_at=last - 10,
        last_activity=last,
        first_user_message=None,
        num_turns=0,
        tokens_in=0,
        tokens_out=0,
        cost=0.0,
        legacy=legacy,
    )


def test_list_returns_empty_when_no_file(tmp_path):
    idx = OrchestratorSessionsIndex(cwd=tmp_path)
    assert idx.list() == []


def test_upsert_then_list_round_trips(tmp_path):
    idx = OrchestratorSessionsIndex(cwd=tmp_path)
    idx.upsert(_entry("a"))
    idx.upsert(_entry("b"))
    out = idx.list()
    assert {e.session_id for e in out} == {"a", "b"}


def test_upsert_replaces_existing_by_session_id(tmp_path):
    idx = OrchestratorSessionsIndex(cwd=tmp_path)
    idx.upsert(_entry("a", last=100.0))
    idx.upsert(_entry("a", last=200.0))
    out = idx.list()
    assert len(out) == 1
    assert out[0].last_activity == 200.0


def test_most_recent_returns_max_last_activity(tmp_path):
    idx = OrchestratorSessionsIndex(cwd=tmp_path)
    idx.upsert(_entry("old", last=100.0))
    idx.upsert(_entry("new", last=300.0))
    idx.upsert(_entry("mid", last=200.0))
    assert idx.most_recent().session_id == "new"


def test_most_recent_is_none_when_empty(tmp_path):
    idx = OrchestratorSessionsIndex(cwd=tmp_path)
    assert idx.most_recent() is None


def test_get_returns_entry_by_session_id(tmp_path):
    idx = OrchestratorSessionsIndex(cwd=tmp_path)
    idx.upsert(_entry("abc"))
    assert idx.get("abc").session_id == "abc"
    assert idx.get("missing") is None


def test_corrupt_file_is_treated_as_empty(tmp_path):
    state = tmp_path / ".patchfeld"
    state.mkdir()
    (state / "orchestrator_sessions.json").write_text("not json {{")
    assert OrchestratorSessionsIndex(cwd=tmp_path).list() == []


def test_index_persists_to_expected_path(tmp_path):
    idx = OrchestratorSessionsIndex(cwd=tmp_path)
    idx.upsert(_entry("a"))
    assert (tmp_path / ".patchfeld" / "orchestrator_sessions.json").exists()
```

- [ ] **Step 2: Run tests to verify failure**

Run: `uv run pytest tests/test_orchestrator_sessions_index.py -v`

Expected: FAIL — module not found.

- [ ] **Step 3: Implement the index**

Create `patchfeld/persistence/orchestrator_sessions.py`:

```python
import json
import logging
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from patchfeld.persistence.atomic import write_json_atomic
from patchfeld.persistence.paths import project_state_dir

log = logging.getLogger(__name__)


@dataclass
class OrchestratorSessionEntry:
    session_id: str
    transcript_path: str
    started_at: float
    last_activity: float
    first_user_message: str | None = None
    num_turns: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost: float = 0.0
    legacy: bool = False


def _index_path(cwd: Path) -> Path:
    return project_state_dir(cwd) / "orchestrator_sessions.json"


class OrchestratorSessionsIndex:
    """Per-cwd index of past orchestrator sessions for resume/picker."""

    def __init__(self, cwd: Path) -> None:
        self._cwd = cwd
        self._path = _index_path(cwd)

    def list(self) -> list[OrchestratorSessionEntry]:
        if not self._path.exists():
            return []
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            if not isinstance(raw, list):
                log.warning("orchestrator_sessions.json is not a list at %s", self._path)
                return []
            valid = {f.name for f in fields(OrchestratorSessionEntry)}
            out: list[OrchestratorSessionEntry] = []
            for item in raw:
                if not isinstance(item, dict):
                    continue
                kwargs = {k: v for k, v in item.items() if k in valid}
                out.append(OrchestratorSessionEntry(**kwargs))
            return out
        except Exception:
            log.exception("Failed to load orchestrator_sessions.json from %s", self._path)
            return []

    def upsert(self, entry: OrchestratorSessionEntry) -> None:
        current = self.list()
        for i, existing in enumerate(current):
            if existing.session_id == entry.session_id:
                current[i] = entry
                self._save(current)
                return
        current.append(entry)
        self._save(current)

    def most_recent(self) -> OrchestratorSessionEntry | None:
        entries = self.list()
        if not entries:
            return None
        return max(entries, key=lambda e: e.last_activity)

    def get(self, session_id: str) -> OrchestratorSessionEntry | None:
        for e in self.list():
            if e.session_id == session_id:
                return e
        return None

    def _save(self, entries: list[OrchestratorSessionEntry]) -> None:
        write_json_atomic(self._path, [asdict(e) for e in entries])
```

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/test_orchestrator_sessions_index.py -v`

Expected: PASS for all 8 tests.

- [ ] **Step 5: Commit**

```bash
git add patchfeld/persistence/orchestrator_sessions.py tests/test_orchestrator_sessions_index.py
git commit -m "feat(persistence): OrchestratorSessionsIndex"
```

---

## Task 5: Legacy migration

**Files:**
- Modify: `patchfeld/persistence/orchestrator_sessions.py`
- Test: `tests/test_orchestrator_sessions_index.py`

If a legacy `.patchfeld/transcripts/orchestrator.jsonl` exists with no
companion index, rename it to `orchestrator.legacy-<ts>.jsonl` and insert
one `legacy=True` entry into the index. Idempotent.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_orchestrator_sessions_index.py`:

```python
def test_migrate_legacy_when_old_jsonl_exists_no_index(tmp_path):
    transcripts = tmp_path / ".patchfeld" / "transcripts"
    transcripts.mkdir(parents=True)
    legacy = transcripts / "orchestrator.jsonl"
    legacy.write_text('{"role": "user", "text": "old"}\n', encoding="utf-8")
    idx = OrchestratorSessionsIndex(cwd=tmp_path)

    idx.migrate_legacy_if_needed()

    assert not legacy.exists()
    renamed = list(transcripts.glob("orchestrator.legacy-*.jsonl"))
    assert len(renamed) == 1
    entries = idx.list()
    assert len(entries) == 1
    assert entries[0].legacy is True
    assert entries[0].session_id.startswith("legacy-")
    assert entries[0].transcript_path.endswith(renamed[0].name)


def test_migrate_legacy_is_idempotent(tmp_path):
    transcripts = tmp_path / ".patchfeld" / "transcripts"
    transcripts.mkdir(parents=True)
    (transcripts / "orchestrator.jsonl").write_text("{}\n", encoding="utf-8")
    idx = OrchestratorSessionsIndex(cwd=tmp_path)
    idx.migrate_legacy_if_needed()
    before = idx.list()
    idx.migrate_legacy_if_needed()
    after = idx.list()
    assert before == after


def test_migrate_legacy_noop_when_no_legacy_file(tmp_path):
    idx = OrchestratorSessionsIndex(cwd=tmp_path)
    idx.migrate_legacy_if_needed()
    assert idx.list() == []


def test_migrate_legacy_noop_when_index_already_exists(tmp_path):
    transcripts = tmp_path / ".patchfeld" / "transcripts"
    transcripts.mkdir(parents=True)
    (transcripts / "orchestrator.jsonl").write_text("{}\n", encoding="utf-8")
    idx = OrchestratorSessionsIndex(cwd=tmp_path)
    idx.upsert(_entry("real"))  # creates orchestrator_sessions.json

    idx.migrate_legacy_if_needed()

    # Legacy file still exists — migration only runs on a clean index.
    assert (transcripts / "orchestrator.jsonl").exists()
    assert {e.session_id for e in idx.list()} == {"real"}
```

- [ ] **Step 2: Run tests to verify failure**

Run: `uv run pytest tests/test_orchestrator_sessions_index.py -v -k migrate`

Expected: FAIL — `OrchestratorSessionsIndex` has no attribute `migrate_legacy_if_needed`.

- [ ] **Step 3: Implement migration**

Add to `OrchestratorSessionsIndex`:

```python
    def migrate_legacy_if_needed(self) -> None:
        """One-time migration: rename .patchfeld/transcripts/orchestrator.jsonl
        to orchestrator.legacy-<ts>.jsonl and register a legacy=True entry.

        No-op if the index already has any entries OR if no legacy file exists.
        """
        from patchfeld.persistence.paths import project_transcripts_dir

        if self._path.exists():
            return  # index already exists — don't touch

        legacy_path = project_transcripts_dir(self._cwd) / "orchestrator.jsonl"
        if not legacy_path.exists():
            return

        mtime = legacy_path.stat().st_mtime
        legacy_id = f"legacy-{int(mtime)}"
        new_path = project_transcripts_dir(self._cwd) / f"orchestrator.{legacy_id}.jsonl"
        legacy_path.rename(new_path)

        entry = OrchestratorSessionEntry(
            session_id=legacy_id,
            transcript_path=str(new_path.relative_to(self._cwd))
                if new_path.is_relative_to(self._cwd) else str(new_path),
            started_at=mtime,
            last_activity=mtime,
            first_user_message=None,
            num_turns=0,
            tokens_in=0,
            tokens_out=0,
            cost=0.0,
            legacy=True,
        )
        self.upsert(entry)
```

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/test_orchestrator_sessions_index.py -v`

Expected: PASS for all migration tests + earlier tests.

- [ ] **Step 5: Commit**

```bash
git add patchfeld/persistence/orchestrator_sessions.py tests/test_orchestrator_sessions_index.py
git commit -m "feat(persistence): one-time legacy orchestrator.jsonl migration"
```

---

## Task 6: Add `OrchestratorSessionSwitched` and `OpenResumePicker` events

**Files:**
- Modify: `patchfeld/events.py`
- Test: `tests/test_events.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_events.py`:

```python
def test_orchestrator_session_switched_event_carries_id_and_path():
    from patchfeld.events import OrchestratorSessionSwitched
    e = OrchestratorSessionSwitched(session_id="abc", transcript_path="/tmp/x.jsonl")
    assert e.session_id == "abc"
    assert e.transcript_path == "/tmp/x.jsonl"


def test_open_resume_picker_event_is_constructible():
    from patchfeld.events import OpenResumePicker
    OpenResumePicker()  # smoke
```

- [ ] **Step 2: Run tests to verify failure**

Run: `uv run pytest tests/test_events.py -v`

Expected: FAIL — import errors.

- [ ] **Step 3: Add events**

Append to `patchfeld/events.py` (under "Built-in event types"):

```python
@dataclass(frozen=True)
class OrchestratorSessionSwitched:
    """The orchestrator session was swapped (via /reset or /resume)."""
    session_id: str
    transcript_path: str


@dataclass(frozen=True)
class OpenResumePicker:
    """Request from the orchestrator that the app open the resume modal."""
    pass
```

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/test_events.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add patchfeld/events.py tests/test_events.py
git commit -m "feat(events): OrchestratorSessionSwitched + OpenResumePicker"
```

---

## Task 7: `OrchestratorSession.start` consults the index

**Files:**
- Modify: `patchfeld/orchestrator/session.py`
- Test: `tests/test_orchestrator_session_resume.py` (new)

When the index has a non-legacy `most_recent()` entry, pass `resume=<id>` and
point the inner transcript at that session's JSONL. Otherwise mint a fresh
`session_id`. Always run `migrate_legacy_if_needed()` first.

- [ ] **Step 1: Read the current `OrchestratorSession.start` to understand what we're modifying**

Run: `sed -n '40,110p' patchfeld/orchestrator/session.py`

Expected: confirms the current options-build flow and where the inner
`AgentSession`/`AgentTranscript` are constructed.

- [ ] **Step 2: Write the failing tests**

Create `tests/test_orchestrator_session_resume.py`:

```python
import pytest
from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    TextBlock,
)

from patchfeld.agents.fake_sdk_adapter import FakeSDKAdapter
from patchfeld.agents.manager import AgentManager
from patchfeld.events import EventBus
from patchfeld.orchestrator.session import OrchestratorSession
from patchfeld.persistence.orchestrator_sessions import (
    OrchestratorSessionEntry,
    OrchestratorSessionsIndex,
)


def _ok_script(session_id: str = "s-fake"):
    return [
        AssistantMessage(content=[TextBlock(text="ok")], model="fake-model"),
        ResultMessage(
            subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
            num_turns=1, session_id=session_id, total_cost_usd=0.0,
            usage={"input_tokens": 1, "output_tokens": 1}, result="ok",
        ),
    ]


class _RecordingAdapter(FakeSDKAdapter):
    """FakeSDKAdapter that records the options it was started with."""

    def __init__(self, scripts):
        super().__init__(scripts)
        self.last_options = None

    async def start(self, *, options):
        self.last_options = options
        await super().start(options=options)


def _build_orch(tmp_path, *, adapter):
    bus = EventBus()
    manager = AgentManager(
        cwd=tmp_path, bus=bus,
        adapter_factory=lambda: FakeSDKAdapter(scripts=[]),
    )
    orch = OrchestratorSession(
        cwd=tmp_path, bus=bus, manager=manager, adapter=adapter,
    )
    return orch, bus


@pytest.mark.asyncio
async def test_start_with_no_prior_sessions_passes_fresh_session_id(tmp_path):
    adapter = _RecordingAdapter(scripts=[_ok_script()])
    orch, _ = _build_orch(tmp_path, adapter=adapter)
    await orch.start()
    try:
        assert adapter.last_options.resume is None
        assert adapter.last_options.session_id is not None
    finally:
        await orch.stop()


@pytest.mark.asyncio
async def test_start_with_prior_session_passes_resume(tmp_path):
    idx = OrchestratorSessionsIndex(cwd=tmp_path)
    idx.upsert(OrchestratorSessionEntry(
        session_id="prev-id", transcript_path="x.jsonl",
        started_at=100.0, last_activity=200.0,
    ))
    adapter = _RecordingAdapter(scripts=[_ok_script(session_id="prev-id")])
    orch, _ = _build_orch(tmp_path, adapter=adapter)
    await orch.start()
    try:
        assert adapter.last_options.resume == "prev-id"
    finally:
        await orch.stop()


@pytest.mark.asyncio
async def test_start_skips_legacy_entries_for_resume(tmp_path):
    idx = OrchestratorSessionsIndex(cwd=tmp_path)
    idx.upsert(OrchestratorSessionEntry(
        session_id="legacy-1", transcript_path="x.jsonl",
        started_at=100.0, last_activity=200.0, legacy=True,
    ))
    adapter = _RecordingAdapter(scripts=[_ok_script()])
    orch, _ = _build_orch(tmp_path, adapter=adapter)
    await orch.start()
    try:
        assert adapter.last_options.resume is None
    finally:
        await orch.stop()


@pytest.mark.asyncio
async def test_start_runs_legacy_migration(tmp_path):
    transcripts = tmp_path / ".patchfeld" / "transcripts"
    transcripts.mkdir(parents=True)
    legacy = transcripts / "orchestrator.jsonl"
    legacy.write_text('{"role": "user", "text": "old"}\n', encoding="utf-8")

    adapter = _RecordingAdapter(scripts=[_ok_script()])
    orch, _ = _build_orch(tmp_path, adapter=adapter)
    await orch.start()
    try:
        assert not legacy.exists()
        assert any(p.name.startswith("orchestrator.legacy-")
                   for p in transcripts.iterdir())
    finally:
        await orch.stop()
```

- [ ] **Step 3: Run tests to verify failure**

Run: `uv run pytest tests/test_orchestrator_session_resume.py -v`

Expected: FAIL — `adapter.last_options.resume` is `None` for the second test
(or AttributeError because we haven't wired anything yet).

- [ ] **Step 4: Implement the index integration**

Edit `patchfeld/orchestrator/session.py`. Add imports:

```python
import uuid

from patchfeld.persistence.orchestrator_sessions import (
    OrchestratorSessionEntry,
    OrchestratorSessionsIndex,
)
from patchfeld.persistence.paths import orchestrator_session_transcript_path
```

Add to `__init__` (after `self._app = app`):

```python
        self._index = OrchestratorSessionsIndex(cwd=cwd)
        self._sdk_session_id: str | None = None
        self._active_transcript_path: Path | None = None
        self._switching_lock = asyncio.Lock()
```

Refactor `start` so the option-building lives in a helper. Replace the
existing `start`:

```python
    async def start(self) -> None:
        # One-time migration of any pre-existing orchestrator.jsonl.
        self._index.migrate_legacy_if_needed()

        # Decide: resume vs new
        prior = self._index.most_recent()
        resume_id: str | None = None
        if prior is not None and not prior.legacy:
            resume_id = prior.session_id
            session_id_for_options = None
            transcript_path = orchestrator_session_transcript_path(
                self._cwd, prior.session_id
            )
            self._sdk_session_id = prior.session_id
        else:
            new_id = uuid.uuid4().hex
            session_id_for_options = new_id
            transcript_path = orchestrator_session_transcript_path(self._cwd, new_id)
            self._sdk_session_id = new_id
        self._active_transcript_path = transcript_path

        await self._build_and_start_inner(
            resume=resume_id, new_session_id=session_id_for_options,
            transcript_path=transcript_path,
        )

        self._unsub_user = self._bus.subscribe(
            UserMessageToOrchestrator, self._on_user_message
        )
        self._unsub_msg = self._bus.subscribe(
            AgentMessageAppended, self._on_message_appended
        )
        self._unsub_notify = self._bus.subscribe(
            AgentNotifiedOrchestrator, self._on_child_notified
        )
        self._unsub_ask = self._bus.subscribe(
            AgentRequestedUserInput, self._on_child_asked
        )

    async def _build_and_start_inner(
        self, *,
        resume: str | None,
        new_session_id: str | None,
        transcript_path: "Path",
    ) -> None:
        mcp_server = build_orchestrator_mcp_server(
            self._manager,
            apply_layout=self._apply_layout,
            layouts_store=self._layouts_store,
            config_store=self._config_store,
            actions=self._actions,
            rebind_keys=self._rebind_keys,
            widget_registry=self._widget_registry,
            current_layout=self._current_layout,
            app=self._app,
        )
        options_kwargs: dict = {
            "cwd": str(self._cwd),
            "mcp_servers": {"patchfeld_orchestrator": mcp_server},
            "permission_mode": "bypassPermissions",
        }
        if resume is not None:
            options_kwargs["resume"] = resume
        if new_session_id is not None:
            options_kwargs["session_id"] = new_session_id
        if self._model is not None:
            options_kwargs["model"] = self._model

        transcript = AgentTranscript(
            cwd=self._cwd, agent_id=self.AGENT_ID, path=transcript_path,
        )
        self._inner = AgentSession(
            info=self._info,
            adapter=self._adapter,
            transcript=transcript,
            bus=self._bus,
            on_session_id=self._on_session_id_observed,
        )
        await self._inner.start(options=ClaudeAgentOptions(**options_kwargs))
```

Add the callback (placeholder for now; Task 12 fleshes it out):

```python
    def _on_session_id_observed(self, session_id: str) -> None:
        self._sdk_session_id = session_id
        # Index upsert added in Task 12.
```

The existing `__init__` constructed `self._inner` eagerly. Remove that
construction — `start` is now responsible. Replace the `__init__` block
that builds `self._inner` with:

```python
        self._inner: AgentSession | None = None  # built in start()
```

Update every `self._inner.X` call site that runs OUTSIDE `start`/`reset`/
`resume`/`stop` to handle the `None` case defensively (they currently
include `interrupt`, `wait_idle`, `stop`, `_on_user_message`, and
`_on_message_appended`):

```python
    async def interrupt(self) -> None:
        if self._inner is not None:
            await self._inner.interrupt()

    async def wait_idle(self) -> None:
        if self._send_tasks:
            pending = [t for t in self._send_tasks if not t.done()]
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            self._send_tasks.clear()
        if self._inner is not None:
            await self._inner.wait_idle()

    async def stop(self) -> None:
        self._unsub_user()
        self._unsub_msg()
        self._unsub_notify()
        self._unsub_ask()
        if self._inner is not None:
            await self._inner.stop()
```

In `_on_user_message`, guard against `None`:

```python
    def _on_user_message(self, event: UserMessageToOrchestrator) -> None:
        if self._inner is None:
            return
        self._send_tasks = [t for t in self._send_tasks if not t.done()]
        task = self._inner.queue_send(event.text)
        self._send_tasks.append(task)
```

Add a `Path` import:

```python
from pathlib import Path
```

(it's already imported — verify; if not, add it.)

- [ ] **Step 5: Run tests to verify pass**

Run: `uv run pytest tests/test_orchestrator_session_resume.py -v`

Expected: PASS for all four tests.

- [ ] **Step 6: Re-run the full test suite to catch regressions**

Run: `uv run pytest -x -q`

Expected: PASS. If anything fails, investigate before moving on — the
`_inner = None`-until-`start` change is the most likely source of breakage.

- [ ] **Step 7: Commit**

```bash
git add patchfeld/orchestrator/session.py tests/test_orchestrator_session_resume.py
git commit -m "feat(orchestrator): consult sessions index on start (resume vs new)"
```

---

## Task 8: `active_transcript_path` accessor + index upsert on session_id

**Files:**
- Modify: `patchfeld/orchestrator/session.py`
- Test: `tests/test_orchestrator_session_resume.py`

When the first `ResultMessage` confirms the session_id, upsert an entry into
the index so the session is resumable on the next launch. Also expose
`active_transcript_path` so `OrchestratorChat` can resolve it on mount.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_orchestrator_session_resume.py`:

```python
@pytest.mark.asyncio
async def test_active_transcript_path_reflects_active_session(tmp_path):
    adapter = _RecordingAdapter(scripts=[_ok_script(session_id="brand-new")])
    orch, _ = _build_orch(tmp_path, adapter=adapter)
    await orch.start()
    try:
        p = orch.active_transcript_path
        assert p is not None
        assert p.name.startswith("orchestrator.")
        assert p.suffix == ".jsonl"
    finally:
        await orch.stop()


@pytest.mark.asyncio
async def test_first_result_message_upserts_index(tmp_path):
    adapter = _RecordingAdapter(scripts=[_ok_script(session_id="confirmed-id")])
    orch, _ = _build_orch(tmp_path, adapter=adapter)
    await orch.start()
    try:
        # Send one message so a ResultMessage flows.
        from patchfeld.events import UserMessageToOrchestrator
        orch._bus.publish(UserMessageToOrchestrator("hi"))
        await orch.wait_idle()

        idx = OrchestratorSessionsIndex(cwd=tmp_path)
        entries = idx.list()
        ids = {e.session_id for e in entries}
        assert "confirmed-id" in ids
        entry = idx.get("confirmed-id")
        assert entry is not None
        assert entry.legacy is False
    finally:
        await orch.stop()
```

- [ ] **Step 2: Run tests to verify failure**

Run: `uv run pytest tests/test_orchestrator_session_resume.py -v -k "active_transcript_path or first_result"`

Expected: FAIL — `active_transcript_path` not defined; index empty.

- [ ] **Step 3: Implement**

Add to `OrchestratorSession`:

```python
    @property
    def active_transcript_path(self) -> "Path | None":
        return self._active_transcript_path
```

Flesh out `_on_session_id_observed`:

```python
    def _on_session_id_observed(self, session_id: str) -> None:
        # Update in-memory pointer to whatever the SDK actually attached us to.
        if self._sdk_session_id != session_id:
            log.warning(
                "orchestrator session_id mismatch: passed %s observed %s",
                self._sdk_session_id, session_id,
            )
            self._sdk_session_id = session_id
            # Re-point the transcript path to match the observed id.
            self._active_transcript_path = orchestrator_session_transcript_path(
                self._cwd, session_id,
            )

        existing = self._index.get(session_id)
        now = time.time()
        if existing is None:
            entry = OrchestratorSessionEntry(
                session_id=session_id,
                transcript_path=str(self._active_transcript_path),
                started_at=self._info.started_at,
                last_activity=now,
                first_user_message=None,
                num_turns=0,
                tokens_in=self._info.tokens_in,
                tokens_out=self._info.tokens_out,
                cost=self._info.cost,
                legacy=False,
            )
        else:
            existing.last_activity = now
            existing.tokens_in = self._info.tokens_in
            existing.tokens_out = self._info.tokens_out
            existing.cost = self._info.cost
            entry = existing
        self._index.upsert(entry)
```

Add `import logging` and `log = logging.getLogger(__name__)` if not present.

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/test_orchestrator_session_resume.py -v`

Expected: PASS for all tests in the file.

- [ ] **Step 5: Commit**

```bash
git add patchfeld/orchestrator/session.py tests/test_orchestrator_session_resume.py
git commit -m "feat(orchestrator): upsert sessions index on first ResultMessage"
```

---

## Task 9: Slash-command parser intercepts `/reset` and `/resume`

**Files:**
- Modify: `patchfeld/orchestrator/session.py`
- Test: `tests/test_orchestrator_session_resume.py`

Parse `/reset`, `/resume`, `/resume <id>` in `_on_user_message` BEFORE
delegating to the inner session. Unknown slash commands fall through.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_orchestrator_session_resume.py`:

```python
@pytest.mark.asyncio
async def test_reset_does_not_send_literal_to_sdk(tmp_path):
    """Sending '/reset' must not appear as a prompt to the SDK."""
    adapter = _RecordingAdapter(scripts=[_ok_script()])
    # Second adapter for the new session after /reset.
    new_adapter = _RecordingAdapter(scripts=[_ok_script(session_id="post-reset")])

    bus = EventBus()
    manager = AgentManager(
        cwd=tmp_path, bus=bus,
        adapter_factory=lambda: FakeSDKAdapter(scripts=[]),
    )
    orch = OrchestratorSession(
        cwd=tmp_path, bus=bus, manager=manager, adapter=adapter,
    )
    # Inject the next adapter the orchestrator should use after reset.
    orch._next_adapter_factory = lambda: new_adapter  # noqa: SLF001 (test-only seam)

    await orch.start()
    try:
        from patchfeld.events import UserMessageToOrchestrator
        bus.publish(UserMessageToOrchestrator("/reset"))
        await orch.wait_idle()

        # The first adapter must not have been queried with "/reset".
        assert adapter._next_query_index == 0
        # The orchestrator's active session changed.
        assert orch._sdk_session_id != "s-fake"
    finally:
        await orch.stop()


@pytest.mark.asyncio
async def test_open_resume_picker_published_on_bare_resume(tmp_path):
    from patchfeld.events import OpenResumePicker, UserMessageToOrchestrator

    adapter = _RecordingAdapter(scripts=[_ok_script()])
    orch, bus = _build_orch(tmp_path, adapter=adapter)
    seen: list[OpenResumePicker] = []
    bus.subscribe(OpenResumePicker, seen.append)

    await orch.start()
    try:
        bus.publish(UserMessageToOrchestrator("/resume"))
        await orch.wait_idle()
        assert len(seen) == 1
    finally:
        await orch.stop()


@pytest.mark.asyncio
async def test_unknown_slash_command_falls_through_to_sdk(tmp_path):
    adapter = _RecordingAdapter(scripts=[_ok_script(), _ok_script()])
    orch, bus = _build_orch(tmp_path, adapter=adapter)
    await orch.start()
    try:
        from patchfeld.events import UserMessageToOrchestrator
        bus.publish(UserMessageToOrchestrator("/help"))
        await orch.wait_idle()
        # Adapter saw the prompt as a query.
        assert adapter._next_query_index == 1
    finally:
        await orch.stop()
```

- [ ] **Step 2: Run tests to verify failure**

Run: `uv run pytest tests/test_orchestrator_session_resume.py -v -k "reset_does_not_send or open_resume_picker or unknown_slash"`

Expected: FAIL — slash parser not implemented.

- [ ] **Step 3: Implement the parser**

Add a regex at module level:

```python
import re

_RESET_RE = re.compile(r"^/reset(?:\s|$)")
_RESUME_BARE_RE = re.compile(r"^/resume\s*$")
_RESUME_ID_RE = re.compile(r"^/resume\s+(\S+)\s*$")
```

In `OrchestratorSession.__init__` add:

```python
        # Test-only seam: when set, used as the adapter factory for the next
        # swap (during /reset or /resume). Production wiring uses RealSDKAdapter.
        self._next_adapter_factory = None
```

Replace `_on_user_message`:

```python
    def _on_user_message(self, event: UserMessageToOrchestrator) -> None:
        if self._inner is None:
            return
        text = event.text
        # Slash-command interception. Only triggers on bare-prefix matches —
        # synthetic messages from child agents are wrapped in "[from agent ...]"
        # and so cannot match.
        if _RESET_RE.match(text):
            asyncio.create_task(self.reset())
            return
        if _RESUME_BARE_RE.match(text):
            self._bus.publish(OpenResumePicker())
            return
        m = _RESUME_ID_RE.match(text)
        if m:
            asyncio.create_task(self.resume(m.group(1)))
            return
        # Fall through: ordinary prompt.
        self._send_tasks = [t for t in self._send_tasks if not t.done()]
        task = self._inner.queue_send(text)
        self._send_tasks.append(task)
```

Add the `OpenResumePicker` import to the imports at the top of the file.

Add a stub `reset()` so the import resolves; full body lands in Task 10:

```python
    async def reset(self) -> None:
        async with self._switching_lock:
            await self._swap_inner(resume=None)

    async def resume(self, session_id: str) -> None:
        # Implemented in Task 11.
        raise NotImplementedError
```

Add a `_swap_inner` stub that the next two tasks will use:

```python
    async def _swap_inner(self, *, resume: str | None) -> None:
        # Stop current, start a new inner with either resume=<id> or a fresh id.
        if self._inner is not None:
            try:
                await self._inner.interrupt()
            except Exception:
                pass
            await self._inner.stop()

        if resume is not None:
            new_session_id = None
            transcript_path = orchestrator_session_transcript_path(self._cwd, resume)
            self._sdk_session_id = resume
        else:
            new_id = uuid.uuid4().hex
            new_session_id = new_id
            transcript_path = orchestrator_session_transcript_path(self._cwd, new_id)
            self._sdk_session_id = new_id
        self._active_transcript_path = transcript_path

        # Pull a fresh adapter. In production this comes from the
        # RealSDKAdapter factory; tests can inject _next_adapter_factory.
        if self._next_adapter_factory is not None:
            self._adapter = self._next_adapter_factory()
            self._next_adapter_factory = None
        else:
            from patchfeld.agents.sdk_adapter import RealSDKAdapter
            self._adapter = RealSDKAdapter()

        await self._build_and_start_inner(
            resume=resume, new_session_id=new_session_id,
            transcript_path=transcript_path,
        )

        self._bus.publish(OrchestratorSessionSwitched(
            session_id=self._sdk_session_id,
            transcript_path=str(self._active_transcript_path),
        ))
```

Import `OrchestratorSessionSwitched` at the top.

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/test_orchestrator_session_resume.py -v`

Expected: PASS for all tests.

- [ ] **Step 5: Run the full suite for regressions**

Run: `uv run pytest -x -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add patchfeld/orchestrator/session.py tests/test_orchestrator_session_resume.py
git commit -m "feat(orchestrator): /reset and /resume slash-command parser + swap scaffolding"
```

---

## Task 10: `/reset` keeps old transcript, creates a new one

**Files:**
- Modify: `patchfeld/orchestrator/session.py`
- Test: `tests/test_orchestrator_session_resume.py`

After the previous task `reset()` already swaps. This task verifies the
durability properties: old JSONL stays on disk, new JSONL exists, an
`OrchestratorSessionSwitched` event fired. Concurrent `/reset` calls
serialize via the lock.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_orchestrator_session_resume.py`:

```python
@pytest.mark.asyncio
async def test_reset_preserves_old_transcript_creates_new(tmp_path):
    from patchfeld.events import (
        OrchestratorSessionSwitched, UserMessageToOrchestrator,
    )

    adapter1 = _RecordingAdapter(scripts=[_ok_script(session_id="first")])
    adapter2 = _RecordingAdapter(scripts=[_ok_script(session_id="second")])

    bus = EventBus()
    manager = AgentManager(
        cwd=tmp_path, bus=bus,
        adapter_factory=lambda: FakeSDKAdapter(scripts=[]),
    )
    orch = OrchestratorSession(cwd=tmp_path, bus=bus, manager=manager, adapter=adapter1)
    orch._next_adapter_factory = lambda: adapter2

    switched: list[OrchestratorSessionSwitched] = []
    bus.subscribe(OrchestratorSessionSwitched, switched.append)

    await orch.start()
    try:
        # First turn so the transcript file exists.
        bus.publish(UserMessageToOrchestrator("hello"))
        await orch.wait_idle()
        old_path = orch.active_transcript_path
        assert old_path.exists()

        bus.publish(UserMessageToOrchestrator("/reset"))
        await orch.wait_idle()
        new_path = orch.active_transcript_path

        assert old_path != new_path
        assert old_path.exists(), "old transcript must remain on disk"
        assert len(switched) == 1
        assert switched[0].session_id == orch._sdk_session_id
    finally:
        await orch.stop()


@pytest.mark.asyncio
async def test_concurrent_resets_serialize(tmp_path):
    """Two /reset calls fired back-to-back must both complete cleanly."""
    from patchfeld.events import UserMessageToOrchestrator

    adapter1 = _RecordingAdapter(scripts=[_ok_script(session_id="first")])
    adapter2 = _RecordingAdapter(scripts=[_ok_script(session_id="second")])
    adapter3 = _RecordingAdapter(scripts=[_ok_script(session_id="third")])

    bus = EventBus()
    manager = AgentManager(
        cwd=tmp_path, bus=bus,
        adapter_factory=lambda: FakeSDKAdapter(scripts=[]),
    )
    orch = OrchestratorSession(cwd=tmp_path, bus=bus, manager=manager, adapter=adapter1)
    factories = iter([lambda: adapter2, lambda: adapter3])
    orch._next_adapter_factory = next(factories)

    # Hook so each swap pulls the next factory.
    original_swap = orch._swap_inner
    async def _swap(*, resume):
        try:
            orch._next_adapter_factory = next(factories)
        except StopIteration:
            pass
        await original_swap(resume=resume)
    orch._swap_inner = _swap  # noqa: SLF001 (test-only)

    await orch.start()
    try:
        bus.publish(UserMessageToOrchestrator("/reset"))
        bus.publish(UserMessageToOrchestrator("/reset"))
        await orch.wait_idle()
        # After two resets the active session is the third.
        assert orch._sdk_session_id == "third"
    finally:
        await orch.stop()
```

- [ ] **Step 2: Run tests to verify pass**

Run: `uv run pytest tests/test_orchestrator_session_resume.py -v -k "reset_preserves or concurrent_resets"`

Expected: PASS — the swap scaffolding from Task 9 already supports this.
If `concurrent_resets` fails because the two `create_task` invocations race
the lock, ensure `reset()` does `async with self._switching_lock:` (it does
per Task 9).

If a test fails, debug — do not move on with red tests.

- [ ] **Step 3: Commit**

```bash
git add tests/test_orchestrator_session_resume.py
git commit -m "test(orchestrator): /reset durability + concurrent-reset serialization"
```

---

## Task 11: `/resume <id>` with SDK-rejection fallback

**Files:**
- Modify: `patchfeld/orchestrator/session.py`
- Test: `tests/test_orchestrator_session_resume.py`

`resume(session_id)` should:
- swap to the requested id with `resume=<id>` in options;
- if the SDK rejects (adapter raises in `start`), fall back to a fresh
  session and notify;
- treat unknown ids as a no-op + notification;
- treat legacy ids as fallback-to-fresh + notification.

For "notification," publish a small `OrchestratorReply`-style line so the
chat shows feedback without needing the App layer.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_orchestrator_session_resume.py`:

```python
@pytest.mark.asyncio
async def test_resume_known_session_passes_resume_to_sdk(tmp_path):
    from patchfeld.events import OrchestratorSessionSwitched

    idx = OrchestratorSessionsIndex(cwd=tmp_path)
    idx.upsert(OrchestratorSessionEntry(
        session_id="target",
        transcript_path=str(tmp_path / ".patchfeld" / "transcripts" / "orchestrator.target.jsonl"),
        started_at=100.0, last_activity=200.0,
    ))

    adapter1 = _RecordingAdapter(scripts=[_ok_script(session_id="boot")])
    adapter2 = _RecordingAdapter(scripts=[_ok_script(session_id="target")])
    bus = EventBus()
    manager = AgentManager(
        cwd=tmp_path, bus=bus,
        adapter_factory=lambda: FakeSDKAdapter(scripts=[]),
    )
    orch = OrchestratorSession(cwd=tmp_path, bus=bus, manager=manager, adapter=adapter1)
    orch._next_adapter_factory = lambda: adapter2
    switched: list[OrchestratorSessionSwitched] = []
    bus.subscribe(OrchestratorSessionSwitched, switched.append)

    await orch.start()
    try:
        await orch.resume("target")
        assert adapter2.last_options.resume == "target"
        assert switched[-1].session_id == "target"
    finally:
        await orch.stop()


@pytest.mark.asyncio
async def test_resume_unknown_session_is_noop_with_notice(tmp_path):
    from patchfeld.events import OrchestratorReply

    adapter = _RecordingAdapter(scripts=[_ok_script()])
    orch, bus = _build_orch(tmp_path, adapter=adapter)
    replies: list[OrchestratorReply] = []
    bus.subscribe(OrchestratorReply, replies.append)

    await orch.start()
    try:
        before = orch._sdk_session_id
        await orch.resume("does-not-exist")
        assert orch._sdk_session_id == before  # no swap
        assert any("no such session" in r.text.lower() for r in replies)
    finally:
        await orch.stop()


@pytest.mark.asyncio
async def test_resume_legacy_falls_back_to_reset(tmp_path):
    from patchfeld.events import OrchestratorReply

    idx = OrchestratorSessionsIndex(cwd=tmp_path)
    idx.upsert(OrchestratorSessionEntry(
        session_id="legacy-99", transcript_path="x.jsonl",
        started_at=100.0, last_activity=200.0, legacy=True,
    ))
    adapter1 = _RecordingAdapter(scripts=[_ok_script(session_id="boot")])
    adapter2 = _RecordingAdapter(scripts=[_ok_script(session_id="fresh")])
    bus = EventBus()
    manager = AgentManager(
        cwd=tmp_path, bus=bus,
        adapter_factory=lambda: FakeSDKAdapter(scripts=[]),
    )
    orch = OrchestratorSession(cwd=tmp_path, bus=bus, manager=manager, adapter=adapter1)
    orch._next_adapter_factory = lambda: adapter2
    replies: list[OrchestratorReply] = []
    bus.subscribe(OrchestratorReply, replies.append)

    await orch.start()
    try:
        await orch.resume("legacy-99")
        assert adapter2.last_options.resume is None  # fresh, not resumed
        assert any("predates" in r.text.lower() or "fresh" in r.text.lower()
                   for r in replies)
    finally:
        await orch.stop()


class _RejectingAdapter(_RecordingAdapter):
    def __init__(self, scripts, reject_resume_id: str):
        super().__init__(scripts)
        self._reject_id = reject_resume_id

    async def start(self, *, options):
        if options.resume == self._reject_id:
            raise RuntimeError("simulated SDK rejection")
        await super().start(options=options)


@pytest.mark.asyncio
async def test_resume_falls_back_when_sdk_rejects(tmp_path):
    from patchfeld.events import OrchestratorReply

    idx = OrchestratorSessionsIndex(cwd=tmp_path)
    idx.upsert(OrchestratorSessionEntry(
        session_id="bad", transcript_path="x.jsonl",
        started_at=100.0, last_activity=200.0,
    ))
    boot_adapter = _RecordingAdapter(scripts=[_ok_script(session_id="boot")])
    rejecting = _RejectingAdapter(scripts=[_ok_script(session_id="bad")], reject_resume_id="bad")
    fresh_adapter = _RecordingAdapter(scripts=[_ok_script(session_id="fresh")])

    bus = EventBus()
    manager = AgentManager(
        cwd=tmp_path, bus=bus,
        adapter_factory=lambda: FakeSDKAdapter(scripts=[]),
    )
    orch = OrchestratorSession(cwd=tmp_path, bus=bus, manager=manager, adapter=boot_adapter)
    factories = iter([lambda: rejecting, lambda: fresh_adapter])
    orch._next_adapter_factory = next(factories)
    original_swap = orch._swap_inner
    async def _swap(*, resume):
        try:
            orch._next_adapter_factory = next(factories)
        except StopIteration:
            pass
        await original_swap(resume=resume)
    orch._swap_inner = _swap

    replies: list[OrchestratorReply] = []
    bus.subscribe(OrchestratorReply, replies.append)

    await orch.start()
    try:
        await orch.resume("bad")
        # Index entry for "bad" preserved.
        assert idx.get("bad") is not None
        # Active session is the fresh fallback.
        assert orch._sdk_session_id == "fresh"
        assert any("could not resume" in r.text.lower() for r in replies)
    finally:
        await orch.stop()
```

- [ ] **Step 2: Run tests to verify failure**

Run: `uv run pytest tests/test_orchestrator_session_resume.py -v -k "resume_known or resume_unknown or resume_legacy or resume_falls_back"`

Expected: FAIL — `resume()` raises `NotImplementedError` and the fallback
isn't wired.

- [ ] **Step 3: Implement `resume`**

Replace the `resume` stub from Task 9:

```python
    async def resume(self, session_id: str) -> None:
        async with self._switching_lock:
            entry = self._index.get(session_id)
            if entry is None:
                self._publish_notice(f"No such session: {session_id}")
                return
            if entry.legacy:
                self._publish_notice(
                    "This session predates SDK resume support; starting a fresh session."
                )
                await self._swap_inner(resume=None)
                return
            try:
                await self._swap_inner(resume=session_id)
            except Exception:
                log.exception("SDK rejected resume=%s; falling back to fresh", session_id)
                self._publish_notice(
                    f"Could not resume {session_id}; starting a fresh session."
                )
                await self._swap_inner(resume=None)

    def _publish_notice(self, text: str) -> None:
        # Surface as an OrchestratorReply so it appears inline in the chat.
        self._bus.publish(OrchestratorReply(text))
```

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/test_orchestrator_session_resume.py -v`

Expected: PASS for the four new tests + no regressions.

- [ ] **Step 5: Commit**

```bash
git add patchfeld/orchestrator/session.py tests/test_orchestrator_session_resume.py
git commit -m "feat(orchestrator): /resume with legacy + SDK-rejection fallback"
```

---

## Task 12: `RichTranscript` accepts a path override and reacts to switches

**Files:**
- Modify: `patchfeld/widgets/rich_transcript.py`
- Create: `tests/test_rich_transcript_replace_source.py`

The widget currently reads from a path derived from `agent_id`. We need:
- optional `transcript_path` ctor arg that overrides replay source;
- `replace_source(path)` method that clears the scroll, drops the current
  turn, and replays from a new path;
- subscription to `OrchestratorSessionSwitched` that calls
  `replace_source(event.transcript_path)`.

`agent_id` filtering for live events stays unchanged.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_rich_transcript_replace_source.py`:

```python
from pathlib import Path

import pytest
from textual.app import App

from patchfeld.events import (
    AgentMessageAppended,
    EventBus,
    OrchestratorSessionSwitched,
)
from patchfeld.persistence.transcript_store import (
    AgentTranscript as Store,
    TranscriptEntry,
)
from patchfeld.widgets.rich_transcript import RichTranscript


class _HostApp(App):
    def __init__(self, bus: EventBus, agent_id: str, path: Path | None) -> None:
        super().__init__()
        self.event_bus = bus
        self._agent_id = agent_id
        self._path = path

    def compose(self):
        yield RichTranscript(
            agent_id=self._agent_id,
            event_bus=self.event_bus,
            transcript_path=self._path,
        )


@pytest.mark.asyncio
async def test_path_override_used_for_replay(tmp_path):
    custom = tmp_path / "custom.jsonl"
    Store(cwd=tmp_path, agent_id="ignored", path=custom).append(
        TranscriptEntry(role="user", text="from custom path")
    )
    Store(cwd=tmp_path, agent_id="ignored", path=custom).append(
        TranscriptEntry(role="assistant", text="hi from custom")
    )

    bus = EventBus()
    app = _HostApp(bus, "orchestrator", custom)
    app.cwd = tmp_path
    async with app.run_test() as pilot:
        await pilot.pause()
        widget = app.query_one(RichTranscript)
        text = widget.rendered_text()
        assert "from custom path" in text
        assert "hi from custom" in text


@pytest.mark.asyncio
async def test_replace_source_clears_and_replays(tmp_path):
    path_a = tmp_path / "a.jsonl"
    path_b = tmp_path / "b.jsonl"
    Store(cwd=tmp_path, agent_id="ignored", path=path_a).append(
        TranscriptEntry(role="user", text="A1"))
    Store(cwd=tmp_path, agent_id="ignored", path=path_b).append(
        TranscriptEntry(role="user", text="B1"))
    Store(cwd=tmp_path, agent_id="ignored", path=path_b).append(
        TranscriptEntry(role="assistant", text="B2"))

    bus = EventBus()
    app = _HostApp(bus, "orchestrator", path_a)
    app.cwd = tmp_path
    async with app.run_test() as pilot:
        await pilot.pause()
        widget = app.query_one(RichTranscript)
        assert "A1" in widget.rendered_text()

        bus.publish(OrchestratorSessionSwitched(
            session_id="ignored", transcript_path=str(path_b),
        ))
        await pilot.pause()

        text = widget.rendered_text()
        assert "B1" in text
        assert "B2" in text
        assert "A1" not in text


@pytest.mark.asyncio
async def test_live_messages_for_agent_id_still_render_after_replace(tmp_path):
    path_a = tmp_path / "a.jsonl"
    path_b = tmp_path / "b.jsonl"
    Store(cwd=tmp_path, agent_id="ignored", path=path_a).append(
        TranscriptEntry(role="user", text="A1"))
    path_b.write_text("", encoding="utf-8")

    bus = EventBus()
    app = _HostApp(bus, "orchestrator", path_a)
    app.cwd = tmp_path
    async with app.run_test() as pilot:
        await pilot.pause()
        bus.publish(OrchestratorSessionSwitched(
            session_id="ignored", transcript_path=str(path_b),
        ))
        await pilot.pause()
        bus.publish(AgentMessageAppended(
            agent_id="orchestrator", role="user", text="live-after-swap"))
        await pilot.pause()
        widget = app.query_one(RichTranscript)
        assert "live-after-swap" in widget.rendered_text()
```

- [ ] **Step 2: Run tests to verify failure**

Run: `uv run pytest tests/test_rich_transcript_replace_source.py -v`

Expected: FAIL — `RichTranscript.__init__` got unexpected kwarg `transcript_path`.

- [ ] **Step 3: Implement**

Edit `patchfeld/widgets/rich_transcript.py`:

```python
class RichTranscript(Vertical):
    """..."""

    def __init__(
        self,
        *,
        agent_id: str,
        event_bus: EventBus | None = None,
        transcript_path: "Path | None" = None,
    ) -> None:
        super().__init__()
        self._agent_id = agent_id
        self._bus = event_bus
        self._transcript_path = transcript_path
        self._unsub_msg = lambda: None
        self._unsub_state = lambda: None
        self._unsub_switched = lambda: None
        self._current_turn: _TurnContainer | None = None
```

Update `on_mount` to use the override path if provided, and subscribe to
the new event:

```python
    def on_mount(self) -> None:
        from patchfeld.events import OrchestratorSessionSwitched
        cwd: Path | None = getattr(self.app, "cwd", None)
        if self._transcript_path is not None:
            store = TranscriptStore(
                cwd=cwd or Path("."), agent_id=self._agent_id,
                path=self._transcript_path,
            )
            for entry in store.read_all():
                self._dispatch_entry(entry)
        elif cwd is not None:
            store = TranscriptStore(cwd=cwd, agent_id=self._agent_id)
            for entry in store.read_all():
                self._dispatch_entry(entry)
        if self._current_turn is not None:
            self._current_turn.mark_done()
            self._current_turn = None
        bus = self._bus or getattr(self.app, "event_bus", None)
        if bus is not None:
            self._unsub_msg = bus.subscribe(AgentMessageAppended, self._on_appended)
            self._unsub_state = bus.subscribe(AgentStateChanged, self._on_state_changed)
            self._unsub_switched = bus.subscribe(
                OrchestratorSessionSwitched, self._on_session_switched,
            )

    def on_unmount(self) -> None:
        self._unsub_msg()
        self._unsub_state()
        self._unsub_switched()
```

Add `replace_source` and `_on_session_switched`:

```python
    def replace_source(self, transcript_path: Path) -> None:
        """Clear the scroll and replay from a new transcript path.

        Called when the orchestrator session is swapped via /reset or /resume.
        Live event filtering still keys off `agent_id` (unchanged).
        """
        self._transcript_path = transcript_path
        scroll = self.query_one(VerticalScroll)
        for child in list(scroll.children):
            child.remove()
        self._current_turn = None
        cwd = getattr(self.app, "cwd", None) or Path(".")
        store = TranscriptStore(
            cwd=cwd, agent_id=self._agent_id, path=transcript_path,
        )
        for entry in store.read_all():
            self._dispatch_entry(entry)
        if self._current_turn is not None:
            self._current_turn.mark_done()
            self._current_turn = None

    def _on_session_switched(self, event) -> None:
        # Filter by agent_id semantics: only the orchestrator transcript reacts.
        if self._agent_id != "orchestrator":
            return
        self.replace_source(Path(event.transcript_path))
```

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/test_rich_transcript_replace_source.py -v`

Expected: PASS for all three tests.

- [ ] **Step 5: Re-run full suite**

Run: `uv run pytest -x -q`

Expected: PASS — existing `RichTranscript` tests should be untouched
(the override is keyword-only and defaults to None).

- [ ] **Step 6: Commit**

```bash
git add patchfeld/widgets/rich_transcript.py tests/test_rich_transcript_replace_source.py
git commit -m "feat(transcript): RichTranscript replace_source + path override"
```

---

## Task 13: `OrchestratorChat` resolves the active path on mount

**Files:**
- Modify: `patchfeld/widgets/orchestrator_chat.py`
- Test: `tests/test_orchestrator_session_resume.py`

`OrchestratorChat` currently passes only `agent_id="orchestrator"` to
`RichTranscript`. Now it should also pass the orchestrator's
`active_transcript_path` so on launch the widget reads from the per-session
JSONL.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_orchestrator_session_resume.py`:

```python
@pytest.mark.asyncio
async def test_orchestrator_chat_uses_active_transcript_path(tmp_path):
    """Smoke: the chat panel renders with the per-session transcript path."""
    from textual.app import App

    from patchfeld.widgets.orchestrator_chat import OrchestratorChat
    from patchfeld.widgets.rich_transcript import RichTranscript
    from patchfeld.persistence.transcript_store import (
        AgentTranscript, TranscriptEntry,
    )

    # Pre-seed an index entry + transcript file so start() resumes.
    idx = OrchestratorSessionsIndex(cwd=tmp_path)
    sid = "preseeded"
    transcript_path = tmp_path / ".patchfeld" / "transcripts" / f"orchestrator.{sid}.jsonl"
    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    AgentTranscript(cwd=tmp_path, agent_id="orchestrator", path=transcript_path).append(
        TranscriptEntry(role="user", text="from-preseed"),
    )
    idx.upsert(OrchestratorSessionEntry(
        session_id=sid, transcript_path=str(transcript_path),
        started_at=100.0, last_activity=200.0,
    ))

    adapter = _RecordingAdapter(scripts=[_ok_script(session_id=sid)])
    orch, bus = _build_orch(tmp_path, adapter=adapter)
    await orch.start()

    class _Host(App):
        def __init__(self, _orch):
            super().__init__()
            self.event_bus = bus
            self.orchestrator = _orch
            self.cwd = tmp_path

        def compose(self):
            yield OrchestratorChat(event_bus=self.event_bus)

    host = _Host(orch)
    try:
        async with host.run_test() as pilot:
            await pilot.pause()
            rich = host.query_one(RichTranscript)
            assert "from-preseed" in rich.rendered_text()
    finally:
        await orch.stop()
```

- [ ] **Step 2: Run test to verify failure**

Run: `uv run pytest tests/test_orchestrator_session_resume.py -v -k orchestrator_chat_uses_active`

Expected: FAIL — `RichTranscript` is constructed without the path; replay
reads the legacy `orchestrator.jsonl` (which doesn't exist) so
`from-preseed` is missing.

- [ ] **Step 3: Implement**

Replace `OrchestratorChat.compose` and update placeholder:

```python
    def compose(self) -> ComposeResult:
        path = None
        try:
            orch = getattr(self.app, "orchestrator", None)
            if orch is not None:
                path = orch.active_transcript_path
        except Exception:
            path = None
        yield RichTranscript(
            agent_id=self.AGENT_ID, event_bus=self._bus, transcript_path=path,
        )
        yield Input(
            placeholder=(
                "Message orchestrator… "
                "(/reset, /resume, ctrl+c to interrupt)"
            ),
            id="orch-input",
        )
```

- [ ] **Step 4: Run test to verify pass**

Run: `uv run pytest tests/test_orchestrator_session_resume.py -v -k orchestrator_chat_uses_active`

Expected: PASS.

- [ ] **Step 5: Re-run full suite**

Run: `uv run pytest -x -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add patchfeld/widgets/orchestrator_chat.py tests/test_orchestrator_session_resume.py
git commit -m "feat(chat): OrchestratorChat uses active transcript path"
```

---

## Task 14: `ResumeScreen` modal

**Files:**
- Create: `patchfeld/widgets/resume_screen.py`
- Create: `tests/test_resume_screen.py`

A modal listing past sessions. Esc dismisses with `None`; Enter dismisses
with the picked `session_id`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_resume_screen.py`:

```python
import pytest
from textual.app import App

from patchfeld.persistence.orchestrator_sessions import (
    OrchestratorSessionEntry,
    OrchestratorSessionsIndex,
)
from patchfeld.widgets.resume_screen import ResumeScreen


def _seed(tmp_path):
    idx = OrchestratorSessionsIndex(cwd=tmp_path)
    idx.upsert(OrchestratorSessionEntry(
        session_id="aaa", transcript_path="x.jsonl",
        started_at=100.0, last_activity=300.0,
        first_user_message="newest one", num_turns=5,
        tokens_in=10, tokens_out=20, cost=0.01,
    ))
    idx.upsert(OrchestratorSessionEntry(
        session_id="bbb", transcript_path="y.jsonl",
        started_at=50.0, last_activity=200.0,
        first_user_message="middle", num_turns=3,
    ))
    idx.upsert(OrchestratorSessionEntry(
        session_id="legacy-1", transcript_path="z.jsonl",
        started_at=10.0, last_activity=100.0,
        legacy=True,
    ))
    return idx


class _Host(App):
    def __init__(self, idx):
        super().__init__()
        self._idx = idx
        self.picked = "unset"

    def on_mount(self):
        def _on_picked(value):
            self.picked = value
        self.push_screen(ResumeScreen(index=self._idx), _on_picked)


@pytest.mark.asyncio
async def test_resume_screen_lists_entries_sorted_by_recency(tmp_path):
    idx = _seed(tmp_path)
    app = _Host(idx)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, ResumeScreen)
        # Expect rows in order: aaa, bbb, legacy-1 (by last_activity desc).
        # _row_session_ids exposes the displayed order for tests.
        assert screen._row_session_ids() == ["aaa", "bbb", "legacy-1"]


@pytest.mark.asyncio
async def test_resume_screen_escape_returns_none(tmp_path):
    idx = _seed(tmp_path)
    app = _Host(idx)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert app.picked is None


@pytest.mark.asyncio
async def test_resume_screen_enter_returns_session_id(tmp_path):
    idx = _seed(tmp_path)
    app = _Host(idx)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Cursor starts on row 0 ("aaa").
        await pilot.press("enter")
        await pilot.pause()
        assert app.picked == "aaa"
```

- [ ] **Step 2: Run tests to verify failure**

Run: `uv run pytest tests/test_resume_screen.py -v`

Expected: FAIL — module not found.

- [ ] **Step 3: Implement**

Create `patchfeld/widgets/resume_screen.py`:

```python
import time

from textual.binding import Binding
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Label

from patchfeld.persistence.orchestrator_sessions import OrchestratorSessionsIndex


class ResumeScreen(ModalScreen[str | None]):
    """Pick a past orchestrator session. Esc dismisses with None;
    Enter dismisses with the selected session_id."""

    DEFAULT_CSS = """
    ResumeScreen {
        align: center middle;
    }
    ResumeScreen > Container {
        width: 80%;
        height: 70%;
        border: thick $primary;
        background: $surface;
        padding: 1 2;
    }
    ResumeScreen DataTable {
        height: 1fr;
    }
    """

    BINDINGS = [
        Binding("escape", "dismiss_none", "cancel"),
        Binding("enter", "select", "resume"),
    ]

    def __init__(self, *, index: OrchestratorSessionsIndex) -> None:
        super().__init__()
        self._index = index
        self._ordered_ids: list[str] = []

    def compose(self):
        with Container():
            yield Label("Resume orchestrator session:")
            yield DataTable(cursor_type="row")
            yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns("when", "first message", "turns", "tokens", "id")
        entries = sorted(self._index.list(), key=lambda e: e.last_activity, reverse=True)
        now = time.time()
        for e in entries:
            table.add_row(
                _relative_time(now - e.last_activity),
                _truncate(e.first_user_message or "(no first message)", 60),
                str(e.num_turns),
                f"{e.tokens_in}/{e.tokens_out}",
                _short_id(e.session_id),
                key=e.session_id,
            )
            self._ordered_ids.append(e.session_id)
        table.focus()

    def _row_session_ids(self) -> list[str]:
        return list(self._ordered_ids)

    def action_dismiss_none(self) -> None:
        self.dismiss(None)

    def action_select(self) -> None:
        table = self.query_one(DataTable)
        if table.row_count == 0:
            self.dismiss(None)
            return
        row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
        self.dismiss(str(row_key.value))

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.dismiss(str(event.row_key.value))


def _relative_time(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)}s ago"
    if seconds < 3600:
        return f"{int(seconds / 60)}m ago"
    if seconds < 86400:
        return f"{int(seconds / 3600)}h ago"
    return f"{int(seconds / 86400)}d ago"


def _truncate(s: str, n: int) -> str:
    if len(s) <= n:
        return s
    return s[: n - 1] + "…"


def _short_id(sid: str) -> str:
    if len(sid) <= 12:
        return sid
    return sid[:8] + "…"
```

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/test_resume_screen.py -v`

Expected: PASS for all three tests.

- [ ] **Step 5: Commit**

```bash
git add patchfeld/widgets/resume_screen.py tests/test_resume_screen.py
git commit -m "feat(widgets): ResumeScreen modal for picking past sessions"
```

---

## Task 15: App handles `OpenResumePicker`

**Files:**
- Modify: `patchfeld/app.py`
- Test: `tests/test_app_smoke_plan2.py` (add a test) or new file
  `tests/test_app_resume_picker.py`

Subscribe to `OpenResumePicker` on mount. On receipt, push a `ResumeScreen`;
on dismiss with a session_id, call `orchestrator.resume(session_id)`. On
dismiss with `None`, no-op. Also update `action_show_help` to mention the
new commands.

- [ ] **Step 1: Write the failing test**

Create `tests/test_app_resume_picker.py`:

```python
import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

from patchfeld.agents.fake_sdk_adapter import FakeSDKAdapter
from patchfeld.agents.manager import AgentManager
from patchfeld.app import PatchfeldApp
from patchfeld.events import EventBus, OpenResumePicker
from patchfeld.orchestrator.session import OrchestratorSession
from patchfeld.persistence.orchestrator_sessions import (
    OrchestratorSessionEntry,
    OrchestratorSessionsIndex,
)
from patchfeld.widgets.resume_screen import ResumeScreen


def _ok():
    return [
        AssistantMessage(content=[TextBlock(text="ok")], model="fake-model"),
        ResultMessage(
            subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
            num_turns=1, session_id="boot", total_cost_usd=0.0,
            usage={"input_tokens": 1, "output_tokens": 1}, result="ok",
        ),
    ]


@pytest.mark.asyncio
async def test_open_resume_picker_pushes_resume_screen(tmp_path):
    # Seed an index entry so the picker has at least one row.
    OrchestratorSessionsIndex(cwd=tmp_path).upsert(OrchestratorSessionEntry(
        session_id="entry-a", transcript_path="x.jsonl",
        started_at=100.0, last_activity=200.0,
    ))

    bus = EventBus()
    manager = AgentManager(
        cwd=tmp_path, bus=bus,
        adapter_factory=lambda: FakeSDKAdapter(scripts=[]),
    )
    app = PatchfeldApp(cwd=tmp_path, manager=manager, global_dir=tmp_path)
    app.event_bus = bus
    app.orchestrator = OrchestratorSession(
        cwd=tmp_path, bus=bus, manager=manager,
        adapter=FakeSDKAdapter(scripts=[_ok()]),
    )

    async with app.run_test() as pilot:
        await pilot.pause()
        bus.publish(OpenResumePicker())
        await pilot.pause()
        assert isinstance(app.screen, ResumeScreen)
```

- [ ] **Step 2: Run test to verify failure**

Run: `uv run pytest tests/test_app_resume_picker.py -v`

Expected: FAIL — `app.screen` is the default screen, not `ResumeScreen`.

- [ ] **Step 3: Implement**

Edit `patchfeld/app.py`. Add an import:

```python
from patchfeld.events import (
    EventBus, OpenResumePicker, TabAdded, TabClosed, TabSwitched,
)
from patchfeld.persistence.orchestrator_sessions import OrchestratorSessionsIndex
from patchfeld.widgets.resume_screen import ResumeScreen
```

In `on_mount`, after the orchestrator starts, subscribe:

```python
        self.event_bus.subscribe(OpenResumePicker, self._on_open_resume_picker)
```

Add the handler:

```python
    def _on_open_resume_picker(self, event) -> None:
        import asyncio as _asyncio
        index = OrchestratorSessionsIndex(cwd=self.cwd)

        def _on_picked(session_id: str | None) -> None:
            if session_id:
                _asyncio.create_task(self.orchestrator.resume(session_id))

        self.push_screen(ResumeScreen(index=index), _on_picked)
```

Update help text:

```python
    def action_show_help(self) -> None:
        self.notify(
            "/ command bar · ctrl-q quit · ctrl-h history · ctrl-l layouts · "
            "ctrl-pgup/pgdn prev/next tab · ctrl-1..9 tab N · ctrl-t new tab · "
            "ctrl-w close tab · /reset new session · /resume past session · ? help",
            title="keybindings",
        )
```

- [ ] **Step 4: Run test to verify pass**

Run: `uv run pytest tests/test_app_resume_picker.py -v`

Expected: PASS.

- [ ] **Step 5: Re-run full suite**

Run: `uv run pytest -x -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add patchfeld/app.py tests/test_app_resume_picker.py
git commit -m "feat(app): handle OpenResumePicker + update help text"
```

---

## Task 16: End-to-end auto-resume across simulated app reloads

**Files:**
- Create: `tests/test_orchestrator_resume_e2e.py`

A focused end-to-end test that's the closest analogue to the bug the user
reported: send a turn in session 1, stop the orchestrator, build a new
orchestrator at the same `cwd`, verify the new SDK options carry
`resume=<previous_id>`.

- [ ] **Step 1: Write the test**

Create `tests/test_orchestrator_resume_e2e.py`:

```python
import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

from patchfeld.agents.fake_sdk_adapter import FakeSDKAdapter
from patchfeld.agents.manager import AgentManager
from patchfeld.events import EventBus, UserMessageToOrchestrator
from patchfeld.orchestrator.session import OrchestratorSession


class _RecordingAdapter(FakeSDKAdapter):
    def __init__(self, scripts):
        super().__init__(scripts)
        self.last_options = None

    async def start(self, *, options):
        self.last_options = options
        await super().start(options=options)


def _script(sid: str):
    return [
        AssistantMessage(content=[TextBlock(text="ok")], model="fake-model"),
        ResultMessage(
            subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
            num_turns=1, session_id=sid, total_cost_usd=0.0,
            usage={"input_tokens": 1, "output_tokens": 1}, result="ok",
        ),
    ]


@pytest.mark.asyncio
async def test_orchestrator_auto_resumes_across_restart(tmp_path):
    bus1 = EventBus()
    manager1 = AgentManager(
        cwd=tmp_path, bus=bus1,
        adapter_factory=lambda: FakeSDKAdapter(scripts=[]),
    )
    adapter1 = _RecordingAdapter(scripts=[_script("seedling")])
    orch1 = OrchestratorSession(
        cwd=tmp_path, bus=bus1, manager=manager1, adapter=adapter1,
    )
    await orch1.start()
    bus1.publish(UserMessageToOrchestrator("hi from session 1"))
    await orch1.wait_idle()
    await orch1.stop()

    # Simulate a fresh app process by constructing a new orchestrator and
    # bus at the same cwd.
    bus2 = EventBus()
    manager2 = AgentManager(
        cwd=tmp_path, bus=bus2,
        adapter_factory=lambda: FakeSDKAdapter(scripts=[]),
    )
    adapter2 = _RecordingAdapter(scripts=[_script("seedling")])
    orch2 = OrchestratorSession(
        cwd=tmp_path, bus=bus2, manager=manager2, adapter=adapter2,
    )
    await orch2.start()
    try:
        assert adapter2.last_options.resume == "seedling"
    finally:
        await orch2.stop()
```

- [ ] **Step 2: Run test to verify pass**

Run: `uv run pytest tests/test_orchestrator_resume_e2e.py -v`

Expected: PASS — this validates the full pipeline (`session_id` capture →
index upsert → next-launch `most_recent` → `resume=` in options).

- [ ] **Step 3: Run the entire test suite**

Run: `uv run pytest -q`

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/test_orchestrator_resume_e2e.py
git commit -m "test(orchestrator): end-to-end auto-resume across simulated restart"
```

---

## Task 17: Manual smoke test in the running app

**Files:** none (manual verification)

Before declaring the feature done, exercise the code path that automated
tests cannot — actually launch `patchfeld`, send a turn, exit, relaunch, and
verify the agent remembers.

- [ ] **Step 1: Launch the app, send a turn, quit**

Run: `uv run patchfeld` (or whatever the project's launch command is —
check `pyproject.toml` for the entry point if unsure).

In the orchestrator chat: type `Remember the number 42 for me.` and press
Enter. Wait for the assistant to acknowledge. Press `ctrl-q` to quit.

- [ ] **Step 2: Re-launch and verify recall**

Run: `uv run patchfeld` again.

In the orchestrator chat: type `What number did I ask you to remember?` —
the agent should answer `42`. If it asks "what number?" or hallucinates,
the resume wiring is broken; do NOT mark this task complete.

- [ ] **Step 3: Verify `/reset`**

Type `/reset`. The chat panel should clear. Type `What number did I ask
you to remember?` — agent should NOT remember 42.

- [ ] **Step 4: Verify `/resume`**

Type `/resume`. The picker modal should appear with at least two rows
(the original session and the post-reset session). Select the original
session. Chat repopulates with the original conversation. Type the
"what number" question — agent answers `42`.

- [ ] **Step 5: Verify legacy migration (optional)**

In a separate cwd that has only an old `.patchfeld/transcripts/orchestrator.jsonl`
and no `orchestrator_sessions.json`, launch `patchfeld` once. Confirm:

```bash
ls .patchfeld/transcripts/
# expect: orchestrator.legacy-<ts>.jsonl   orchestrator.<new_id>.jsonl
ls .patchfeld/orchestrator_sessions.json
# expect: file exists, with one legacy entry and one fresh entry
```

- [ ] **Step 6: If any step above failed, do NOT mark complete**

Open an issue, capture the failure, and fix before commit. If everything
passed, commit a one-line marker:

```bash
git commit --allow-empty -m "chore: manual smoke of orchestrator resume passed"
```

---

## Self-Review

I checked the plan against the spec:

**Spec coverage:**
- Goal (auto-resume + /reset + /resume) → Tasks 7, 9, 10, 11, 16, 17
- Per-cwd index → Task 4
- Per-session JSONL filenames → Tasks 1, 2, 7
- Legacy migration → Task 5
- `AgentSession.session_id` capture → Task 3
- `RichTranscript.replace_source` + path override → Task 12
- `OrchestratorSessionSwitched` + `OpenResumePicker` events → Task 6
- `ResumeScreen` modal → Task 14
- App `push_screen(ResumeScreen)` wiring + help text → Task 15
- `OrchestratorChat` placeholder + path resolve → Task 13
- SDK-rejection fallback for `resume=` → Task 11
- Concurrent `/reset` lock → Task 10 (uses lock added in Task 9)
- E2E auto-resume across restart → Task 16
- Manual smoke → Task 17

All spec sections are covered.

**Placeholder scan:** no TBD/TODO; every code step shows the actual code;
every test step shows the actual test; every command shows the exact
invocation and expected result.

**Type / signature consistency:**
- `AgentTranscript(cwd, agent_id, *, path=None)` defined Task 1, used
  consistently in Tasks 7, 12, 13, 14.
- `AgentSession(..., on_session_id=...)` defined Task 3, used in Task 7.
- `OrchestratorSessionEntry` and `OrchestratorSessionsIndex` API defined
  Task 4, used in Tasks 5, 7, 8, 11, 14, 15.
- `OrchestratorSessionSwitched(session_id, transcript_path)` defined Task 6,
  used in Tasks 9, 10, 12.
- `RichTranscript(..., transcript_path=None)` and `replace_source(path)`
  defined Task 12, used in Task 13.
- `OrchestratorSession.active_transcript_path`, `.reset()`, `.resume(sid)`,
  `._swap_inner()` defined progressively in Tasks 7–11; later tasks use
  them consistently.

No type drift detected.

---

**Plan complete and saved to `docs/superpowers/plans/2026-05-06-orchestrator-session-resume.md`.**
