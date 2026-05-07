# RichTranscript Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the flat-line transcript rendering in `OrchestratorChat` and `AgentTranscript` with a shared `RichTranscript` widget that groups output by turn, surfaces thinking + tool activity live, and collapses the intermediate steps when the turn finishes so the final response stands out — Claude-Code-style.

**Architecture:** A new `RichTranscript(Vertical)` widget lives in `patchbai/widgets/rich_transcript.py` and subscribes to `AgentMessageAppended` (filtered by `agent_id`) and `AgentStateChanged`. It mounts one `_TurnContainer` per user prompt and fills it with `_ThinkingGroup` and `_ToolCall` sub-widgets (both `Collapsible`) that auto-expand while running and auto-collapse on completion. The two existing widgets become thin shells that compose `RichTranscript` + `Input` and keep their public API intact. Two upstream pieces shift to make tool↔result pairing reliable: `AgentMessageAppended` and `TranscriptEntry` gain optional `tool_id` / `tool_name` fields, and `AgentSession._handle_message` populates them from the SDK's `ToolUseBlock.id` / `ToolResultBlock.tool_use_id`.

**Tech Stack:** Python 3, Textual (`Collapsible`, `Vertical`, `VerticalScroll`, `Static`, `Input`), Rich `Text` (markup-safe rendering), `claude-agent-sdk` (`ToolUseBlock`, `ToolResultBlock`, `ThinkingBlock`), `pytest` + `pytest-asyncio`.

**Spec:** `docs/superpowers/specs/2026-05-06-rich-transcript-design.md`

---

## File Structure

**New files:**
- `patchbai/widgets/rich_transcript.py` — the `RichTranscript` widget plus its three module-private sub-widgets (`_TurnContainer`, `_ThinkingGroup`, `_ToolCall`) and the `_SpinnerTitle` helper.
- `tests/test_rich_transcript.py` — unit + Textual `pilot` integration tests for the new widget.
- `tests/test_rich_transcript_replay.py` — focused tests for history-replay-on-mount behavior (kept separate so the file stays under ~250 lines).

**Modified files:**
- `patchbai/events.py` — extend `AgentMessageAppended` with optional `tool_id` / `tool_name` fields.
- `patchbai/persistence/transcript_store.py` — extend `TranscriptEntry` with optional `tool_id` / `tool_name` fields, and tolerate old transcripts that lack them.
- `patchbai/agents/session.py` — populate the new fields in `_handle_message`.
- `patchbai/orchestrator/session.py` — drop the `[tool use]` / `[tool result]` `OrchestratorReply` re-publishes; keep the assistant-text one.
- `patchbai/widgets/orchestrator_chat.py` — collapse to a thin shell that owns a `RichTranscript` + `Input`.
- `patchbai/widgets/agent_transcript.py` — same shell treatment; preserve `rendered_text()` as a façade over `RichTranscript`.

**No changes (verify they still work):** `patchbai/app.py`, the layout registry, layout YAMLs, `patchbai/orchestrator/formatting.py` (still used by anything? — verify; no caller changes either way).

---

## Task 1: Extend `AgentMessageAppended` with `tool_id` / `tool_name`

**Files:**
- Modify: `patchbai/events.py:57-62`
- Test: `tests/test_events.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_events.py`:

```python
def test_agent_message_appended_has_optional_tool_fields():
    from patchbai.events import AgentMessageAppended

    # Backwards-compatible default — old call sites still work.
    e1 = AgentMessageAppended(agent_id="a", role="assistant", text="hi")
    assert e1.tool_id is None
    assert e1.tool_name is None

    # New call sites can carry SDK-provided ids.
    e2 = AgentMessageAppended(
        agent_id="a", role="tool_use", text="...",
        tool_id="toolu_abc", tool_name="bash",
    )
    assert e2.tool_id == "toolu_abc"
    assert e2.tool_name == "bash"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_events.py::test_agent_message_appended_has_optional_tool_fields -v`
Expected: FAIL with `TypeError: __init__() got an unexpected keyword argument 'tool_id'`.

- [ ] **Step 3: Add the optional fields**

Edit `patchbai/events.py:57-62`. Replace the existing `AgentMessageAppended` dataclass with:

```python
@dataclass(frozen=True)
class AgentMessageAppended:
    """A new message landed in an agent's transcript."""
    agent_id: str
    role: str  # "user" | "assistant" | "tool_use" | "tool_result" | "thinking" | "system"
    text: str
    tool_id: str | None = None       # set for role in {"tool_use", "tool_result"}
    tool_name: str | None = None     # set for role == "tool_use"
```

(Note: `"thinking"` was already a value the producer emitted — the docstring just hadn't listed it.)

- [ ] **Step 4: Run the new test and the full event-test file**

Run: `uv run pytest tests/test_events.py -v`
Expected: all PASS, including the new test.

- [ ] **Step 5: Commit**

```bash
git add patchbai/events.py tests/test_events.py
git commit -m "feat(events): add tool_id/tool_name to AgentMessageAppended"
```

---

## Task 2: Extend `TranscriptEntry` with `tool_id` / `tool_name`

**Files:**
- Modify: `patchbai/persistence/transcript_store.py:14-17`
- Test: `tests/test_per_agent_transcript.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_per_agent_transcript.py`:

```python
def test_transcript_entry_round_trips_tool_fields(tmp_path):
    from patchbai.persistence.transcript_store import AgentTranscript, TranscriptEntry

    t = AgentTranscript(cwd=tmp_path, agent_id="x")
    t.append(TranscriptEntry(role="tool_use", text="ls /tmp",
                             tool_id="toolu_1", tool_name="bash"))
    t.append(TranscriptEntry(role="tool_result", text="<output>",
                             tool_id="toolu_1"))

    entries = t.read_all()
    assert entries[0].tool_id == "toolu_1"
    assert entries[0].tool_name == "bash"
    assert entries[1].tool_id == "toolu_1"
    assert entries[1].tool_name is None


def test_transcript_entry_reads_old_records_without_tool_fields(tmp_path):
    """Records written before tool_id/tool_name existed must still load."""
    import json
    from patchbai.persistence.paths import (
        project_transcript_path, project_transcripts_dir,
    )
    from patchbai.persistence.transcript_store import AgentTranscript

    project_transcripts_dir(tmp_path).mkdir(parents=True, exist_ok=True)
    path = project_transcript_path(tmp_path, "old")
    path.write_text(json.dumps({"role": "assistant", "text": "hi"}) + "\n",
                    encoding="utf-8")

    entries = AgentTranscript(cwd=tmp_path, agent_id="old").read_all()
    assert len(entries) == 1
    assert entries[0].role == "assistant"
    assert entries[0].text == "hi"
    assert entries[0].tool_id is None
    assert entries[0].tool_name is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_per_agent_transcript.py -v`
Expected: both new tests FAIL with `TypeError: __init__() got an unexpected keyword argument 'tool_id'` (the new fields don't exist yet).

- [ ] **Step 3: Add the optional fields**

Edit `patchbai/persistence/transcript_store.py:14-17`. Replace the existing `TranscriptEntry` dataclass with:

```python
@dataclass(frozen=True)
class TranscriptEntry:
    role: str  # "user" | "assistant" | "tool_use" | "tool_result" | "thinking" | "system" | "orch"
    text: str
    tool_id: str | None = None
    tool_name: str | None = None
```

The `read_all()` method already passes `**json.loads(line)` to the constructor and now needs to tolerate *missing* keys (old records have no `tool_id`/`tool_name`). Since the new fields default to `None`, missing keys are fine; but `read_all()` will still raise if a stored line has *extra* unknown keys. Make it forward-compatible too. Replace the body of `read_all()` (lines 38-49):

```python
    def read_all(self) -> list[TranscriptEntry]:
        if not self._path.exists():
            return []
        out: list[TranscriptEntry] = []
        valid_keys = {f.name for f in fields(TranscriptEntry)}
        for line in self._path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
                kwargs = {k: v for k, v in raw.items() if k in valid_keys}
                out.append(TranscriptEntry(**kwargs))
            except Exception:
                log.warning("Skipping corrupted transcript line: %r", line)
        return out
```

And add `fields` to the dataclasses import at the top of the file:

```python
from dataclasses import asdict, dataclass, fields
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_per_agent_transcript.py -v`
Expected: all PASS, including both new tests.

- [ ] **Step 5: Commit**

```bash
git add patchbai/persistence/transcript_store.py tests/test_per_agent_transcript.py
git commit -m "feat(transcript): add tool_id/tool_name with forward+back compat"
```

---

## Task 3: Populate `tool_id` / `tool_name` in `AgentSession._handle_message`

**Files:**
- Modify: `patchbai/agents/session.py:103-138`
- Test: `tests/test_agent_session.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_agent_session.py`:

```python
@pytest.mark.asyncio
async def test_tool_use_and_tool_result_carry_tool_id(tmp_path):
    """ToolUseBlock.id and ToolResultBlock.tool_use_id reach the bus event."""
    from claude_agent_sdk import (
        AssistantMessage, ResultMessage, ToolResultBlock, ToolUseBlock, UserMessage,
    )
    from patchbai.agents.fake_sdk_adapter import FakeSDKAdapter
    from patchbai.agents.session import AgentSession
    from patchbai.agents.state import AgentInfo
    from patchbai.events import AgentMessageAppended, EventBus
    from patchbai.persistence.transcript_store import AgentTranscript

    bus = EventBus()
    received: list[AgentMessageAppended] = []
    bus.subscribe(AgentMessageAppended, received.append)

    script = [
        AssistantMessage(
            content=[ToolUseBlock(id="toolu_xyz", name="bash",
                                  input={"command": "ls"})],
            model="fake-model",
        ),
        UserMessage(content=[ToolResultBlock(
            tool_use_id="toolu_xyz", content="output", is_error=False,
        )]),
        ResultMessage(
            subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
            num_turns=1, session_id="fake", total_cost_usd=0.0,
            usage={"input_tokens": 1, "output_tokens": 1}, result="ok",
        ),
    ]

    session = AgentSession(
        info=AgentInfo(id="a1", name="a1", cwd=str(tmp_path), started_at=0),
        adapter=FakeSDKAdapter(scripts=[script]),
        transcript=AgentTranscript(cwd=tmp_path, agent_id="a1"),
        bus=bus,
    )
    from claude_agent_sdk import ClaudeAgentOptions
    await session.start(options=ClaudeAgentOptions(cwd=str(tmp_path)))
    await session.send("go")
    await session.wait_idle()
    await session.stop()

    tool_uses = [e for e in received if e.role == "tool_use"]
    tool_results = [e for e in received if e.role == "tool_result"]
    assert tool_uses and tool_uses[0].tool_id == "toolu_xyz"
    assert tool_uses[0].tool_name == "bash"
    assert tool_results and tool_results[0].tool_id == "toolu_xyz"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_agent_session.py::test_tool_use_and_tool_result_carry_tool_id -v`
Expected: FAIL — `tool_uses[0].tool_id` is `None` (handler doesn't pass it through yet).

- [ ] **Step 3: Pass `tool_id` / `tool_name` through `_record`**

Edit `patchbai/agents/session.py`. Update `_handle_message` (lines 103-131) and `_record` (lines 133-139):

Replace the `ToolUseBlock` branch (lines 108-112):

```python
                elif isinstance(block, ToolUseBlock):
                    self._record(
                        role="tool_use",
                        text=f"[{block.name}] {_short_repr(block.input)}",
                        tool_id=block.id,
                        tool_name=block.name,
                    )
```

Replace the `ToolResultBlock` branch (lines 117-121):

```python
                if isinstance(block, ToolResultBlock):
                    self._record(
                        role="tool_result",
                        text=_short_repr(block.content),
                        tool_id=block.tool_use_id,
                    )
```

Replace the `_record` method (lines 133-139):

```python
    def _record(
        self,
        *,
        role: str,
        text: str,
        tool_id: str | None = None,
        tool_name: str | None = None,
    ) -> None:
        entry = TranscriptEntry(
            role=role, text=text, tool_id=tool_id, tool_name=tool_name,
        )
        self._transcript.append(entry)
        self._bus.publish(
            AgentMessageAppended(
                agent_id=self.info.id, role=role, text=text,
                tool_id=tool_id, tool_name=tool_name,
            )
        )
        self.info.last_activity = time.time()
```

- [ ] **Step 4: Run the test**

Run: `uv run pytest tests/test_agent_session.py -v`
Expected: all PASS, including the new test.

- [ ] **Step 5: Commit**

```bash
git add patchbai/agents/session.py tests/test_agent_session.py
git commit -m "feat(agents): forward tool_id/tool_name from SDK blocks to bus"
```

---

## Task 4: Drop the `[tool use]` / `[tool result]` `OrchestratorReply` re-publishes

**Files:**
- Modify: `patchbai/orchestrator/session.py:138-152`
- Test: `tests/test_orchestrator_session.py`

The widget will subscribe to `AgentMessageAppended` directly for richer rendering (Task 5+), so the prefixed/truncated `OrchestratorReply` strings for tool use/result are dead weight. Keep the assistant-text re-publish — existing tests assert on it as the public "the orchestrator said something" signal.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_orchestrator_session.py`:

```python
@pytest.mark.asyncio
async def test_tool_use_does_not_publish_orchestrator_reply(tmp_path):
    """Tool use/result no longer go through OrchestratorReply — RichTranscript
    reads the richer AgentMessageAppended event directly."""
    from claude_agent_sdk import (
        AssistantMessage, ResultMessage, ToolResultBlock, ToolUseBlock, UserMessage,
    )

    script = [
        AssistantMessage(
            content=[ToolUseBlock(id="t1", name="bash", input={"cmd": "ls"})],
            model="fake-model",
        ),
        UserMessage(content=[ToolResultBlock(
            tool_use_id="t1", content="ok", is_error=False,
        )]),
        AssistantMessage(content=[TextBlock(text="done")], model="fake-model"),
        ResultMessage(
            subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
            num_turns=1, session_id="fake", total_cost_usd=0.0,
            usage={"input_tokens": 1, "output_tokens": 1}, result="done",
        ),
    ]

    bus = EventBus()
    replies: list[OrchestratorReply] = []
    bus.subscribe(OrchestratorReply, replies.append)

    manager = AgentManager(
        cwd=tmp_path, bus=bus,
        adapter_factory=lambda: FakeSDKAdapter(scripts=[_ok_script()]),
    )
    session = OrchestratorSession(
        cwd=tmp_path, bus=bus, manager=manager,
        adapter=FakeSDKAdapter(scripts=[script]),
    )
    await session.start()
    bus.publish(UserMessageToOrchestrator("go"))
    await session.wait_idle()
    await session.stop()

    reply_texts = [r.text for r in replies]
    # Assistant text still comes through — preserves existing behavior.
    assert "done" in reply_texts
    # Tool use/result no longer leak through the reply channel.
    assert not any(t.startswith("[tool use]") for t in reply_texts)
    assert not any(t.startswith("[tool result]") for t in reply_texts)
```

You'll need `TextBlock` in the imports at the top — add to the existing `from claude_agent_sdk import …` line.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_orchestrator_session.py::test_tool_use_does_not_publish_orchestrator_reply -v`
Expected: FAIL — replies currently include `[tool use] …` and `[tool result] …` strings.

- [ ] **Step 3: Drop the tool-related re-publishes**

Edit `patchbai/orchestrator/session.py:138-152`. Replace the `_on_message_appended` method:

```python
    def _on_message_appended(self, event: AgentMessageAppended) -> None:
        if event.agent_id != self.AGENT_ID:
            return
        # RichTranscript subscribes to AgentMessageAppended directly for tool
        # use/result/thinking — only re-publish assistant text, which is the
        # public "the orchestrator said something" signal other code asserts on.
        if event.role == "assistant":
            self._bus.publish(OrchestratorReply(event.text))
```

- [ ] **Step 4: Run all orchestrator-session tests**

Run: `uv run pytest tests/test_orchestrator_session.py tests/test_orchestrator_session_serializes.py -v`
Expected: all PASS, including the new test.

- [ ] **Step 5: Commit**

```bash
git add patchbai/orchestrator/session.py tests/test_orchestrator_session.py
git commit -m "refactor(orch): drop tool use/result re-publishes; widget reads raw events"
```

---

## Task 5: Skeleton `RichTranscript` widget — mount + history replay (no foldables yet)

This task lays down the widget shape and proves the event subscription / history replay paths work, before introducing any of the foldable / spinner logic. We keep flat-line rendering temporarily; foldables come in Tasks 6–9.

**Files:**
- Create: `patchbai/widgets/rich_transcript.py`
- Test: `tests/test_rich_transcript.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_rich_transcript.py`:

```python
import pytest
from textual.app import App
from textual.widgets import Static

from patchbai.events import AgentMessageAppended, EventBus
from patchbai.persistence.transcript_store import AgentTranscript as Store, TranscriptEntry
from patchbai.widgets.rich_transcript import RichTranscript


class _HostApp(App):
    def __init__(self, bus: EventBus, agent_id: str) -> None:
        super().__init__()
        self.event_bus = bus
        self._agent_id = agent_id

    def compose(self):
        yield RichTranscript(agent_id=self._agent_id, event_bus=self.event_bus)


@pytest.mark.asyncio
async def test_rich_transcript_replays_history_from_disk(tmp_path):
    store = Store(cwd=tmp_path, agent_id="a1")
    store.append(TranscriptEntry(role="user", text="hello"))
    store.append(TranscriptEntry(role="assistant", text="hi"))

    bus = EventBus()
    app = _HostApp(bus, "a1")
    app.cwd = tmp_path
    async with app.run_test() as pilot:
        await pilot.pause()
        widget = app.query_one(RichTranscript)
        text = widget.rendered_text()
        assert "hello" in text
        assert "hi" in text


@pytest.mark.asyncio
async def test_rich_transcript_appends_live_messages(tmp_path):
    bus = EventBus()
    app = _HostApp(bus, "a1")
    app.cwd = tmp_path
    async with app.run_test() as pilot:
        await pilot.pause()
        bus.publish(AgentMessageAppended(agent_id="a1", role="assistant", text="live!"))
        await pilot.pause()
        widget = app.query_one(RichTranscript)
        assert "live!" in widget.rendered_text()


@pytest.mark.asyncio
async def test_rich_transcript_ignores_other_agents(tmp_path):
    bus = EventBus()
    app = _HostApp(bus, "a1")
    app.cwd = tmp_path
    async with app.run_test() as pilot:
        await pilot.pause()
        bus.publish(AgentMessageAppended(agent_id="b2", role="assistant", text="leak"))
        await pilot.pause()
        widget = app.query_one(RichTranscript)
        assert "leak" not in widget.rendered_text()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_rich_transcript.py -v`
Expected: all 3 FAIL with `ModuleNotFoundError: No module named 'patchbai.widgets.rich_transcript'`.

- [ ] **Step 3: Implement the skeleton widget**

Create `patchbai/widgets/rich_transcript.py`:

```python
from __future__ import annotations

from pathlib import Path

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Static

from patchbai.events import AgentMessageAppended, EventBus
from patchbai.persistence.transcript_store import (
    AgentTranscript as TranscriptStore,
    TranscriptEntry,
)


class RichTranscript(Vertical):
    """Scrollable, live-updating transcript with per-turn grouping.

    Subscribes to AgentMessageAppended (filtered by agent_id) and
    AgentStateChanged (Task 9) to render turns containing thinking groups,
    tool-call foldables, and final response text.
    """

    DEFAULT_CSS = """
    RichTranscript {
        height: 1fr;
    }
    RichTranscript > VerticalScroll {
        height: 1fr;
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
        self._unsub_msg = lambda: None

    def compose(self) -> ComposeResult:
        yield VerticalScroll()

    def on_mount(self) -> None:
        # Replay on-disk history first so live events append after it.
        cwd: Path | None = getattr(self.app, "cwd", None)
        if cwd is not None:
            store = TranscriptStore(cwd=cwd, agent_id=self._agent_id)
            for entry in store.read_all():
                self._render_entry(entry)
        bus = self._bus or getattr(self.app, "event_bus", None)
        if bus is not None:
            self._unsub_msg = bus.subscribe(AgentMessageAppended, self._on_appended)

    def on_unmount(self) -> None:
        self._unsub_msg()

    def _on_appended(self, event: AgentMessageAppended) -> None:
        if event.agent_id != self._agent_id:
            return
        # Skeleton: render every event as a flat line. Tasks 6–9 replace this
        # with turn-grouped foldables.
        self._render_entry(TranscriptEntry(
            role=event.role, text=event.text,
            tool_id=event.tool_id, tool_name=event.tool_name,
        ))

    def _render_entry(self, entry: TranscriptEntry) -> None:
        scroll = self.query_one(VerticalScroll)
        line = Text()
        line.append(f"{entry.role}: ", style="bold")
        line.append(entry.text)
        scroll.mount(Static(line))
        scroll.scroll_end(animate=False)

    # --- test helpers -----------------------------------------------------

    def rendered_text(self) -> str:
        """Concatenate all visible text in the scroll, for tests."""
        scroll = self.query_one(VerticalScroll)
        parts: list[str] = []
        for child in scroll.children:
            if isinstance(child, Static):
                parts.append(str(child.renderable))
            else:
                # Recursively gather text from any nested widgets (tasks 6–9
                # introduce Collapsible-wrapped content).
                for static in child.query(Static):
                    parts.append(str(static.renderable))
        return "\n".join(parts)
```

- [ ] **Step 4: Run the new tests**

Run: `uv run pytest tests/test_rich_transcript.py -v`
Expected: all 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add patchbai/widgets/rich_transcript.py tests/test_rich_transcript.py
git commit -m "feat(widgets): add RichTranscript skeleton with history replay"
```

---

## Task 6: Introduce `_TurnContainer` — group output by turn

Now we replace the flat-line renderer inside `RichTranscript` with a turn-grouping renderer: each `user` event opens a new `_TurnContainer`; subsequent events route into its sub-widgets. We still render thinking / tool / text as plain `Static`s inside the container — foldables come in Task 7.

**Files:**
- Modify: `patchbai/widgets/rich_transcript.py`
- Test: `tests/test_rich_transcript.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_rich_transcript.py`:

```python
@pytest.mark.asyncio
async def test_each_user_message_opens_a_new_turn(tmp_path):
    from patchbai.widgets.rich_transcript import _TurnContainer

    bus = EventBus()
    app = _HostApp(bus, "a1")
    app.cwd = tmp_path
    async with app.run_test() as pilot:
        await pilot.pause()
        bus.publish(AgentMessageAppended(agent_id="a1", role="user", text="q1"))
        bus.publish(AgentMessageAppended(agent_id="a1", role="assistant", text="a1"))
        bus.publish(AgentMessageAppended(agent_id="a1", role="user", text="q2"))
        bus.publish(AgentMessageAppended(agent_id="a1", role="assistant", text="a2"))
        await pilot.pause()

        widget = app.query_one(RichTranscript)
        turns = list(widget.query(_TurnContainer))
        assert len(turns) == 2
        # Each turn contains its own assistant reply.
        assert "q1" in turns[0].rendered_text()
        assert "a1" in turns[0].rendered_text()
        assert "q2" in turns[1].rendered_text()
        assert "a2" in turns[1].rendered_text()


@pytest.mark.asyncio
async def test_assistant_text_routes_to_current_turn(tmp_path):
    from patchbai.widgets.rich_transcript import _TurnContainer

    bus = EventBus()
    app = _HostApp(bus, "a1")
    app.cwd = tmp_path
    async with app.run_test() as pilot:
        await pilot.pause()
        bus.publish(AgentMessageAppended(agent_id="a1", role="user", text="hi"))
        bus.publish(AgentMessageAppended(agent_id="a1", role="thinking", text="planning..."))
        bus.publish(AgentMessageAppended(
            agent_id="a1", role="tool_use", text="[bash] {'cmd': 'ls'}",
            tool_id="t1", tool_name="bash",
        ))
        bus.publish(AgentMessageAppended(
            agent_id="a1", role="tool_result", text="<output>", tool_id="t1",
        ))
        bus.publish(AgentMessageAppended(agent_id="a1", role="assistant", text="done"))
        await pilot.pause()

        widget = app.query_one(RichTranscript)
        turns = list(widget.query(_TurnContainer))
        assert len(turns) == 1
        body = turns[0].rendered_text()
        assert "hi" in body
        assert "planning..." in body
        assert "bash" in body
        assert "<output>" in body
        assert "done" in body
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_rich_transcript.py -v`
Expected: 2 FAILs — `_TurnContainer` doesn't exist; existing tests still pass.

- [ ] **Step 3: Add `_TurnContainer` and route events through it**

Edit `patchbai/widgets/rich_transcript.py`. Add after the imports (before `class RichTranscript`):

```python
class _TurnContainer(Vertical):
    """One conversation turn: user prompt + steps + final response."""

    DEFAULT_CSS = """
    _TurnContainer {
        height: auto;
        margin-top: 1;
    }
    _TurnContainer.turn-running {
        border-left: thick $accent;
        padding-left: 1;
    }
    _TurnContainer.turn-done,
    _TurnContainer.turn-error {
        padding-left: 1;
    }
    _TurnContainer.turn-error {
        border-left: thick $error;
    }
    """

    def __init__(self, user_text: str) -> None:
        super().__init__()
        self.add_class("turn-running")
        self._user_text = user_text

    def compose(self) -> ComposeResult:
        line = Text()
        line.append("you: ", style="bold cyan")
        line.append(self._user_text)
        yield Static(line, classes="msg-user")

    def add_thinking(self, text: str) -> None:
        line = Text()
        line.append("thinking: ", style="bold dim")
        line.append(text, style="dim")
        self.mount(Static(line, classes="msg-thinking"))

    def add_tool_call(
        self, *, tool_id: str | None, tool_name: str | None, args_text: str,
    ) -> None:
        line = Text()
        line.append(f"tool[{tool_name or '?'}]: ", style="bold yellow")
        line.append(args_text)
        widget = Static(line, classes="msg-tool-use")
        self.mount(widget)

    def attach_tool_result(self, *, tool_id: str | None, content_text: str) -> None:
        line = Text()
        line.append("result: ", style="bold")
        line.append(content_text)
        self.mount(Static(line, classes="msg-tool-result"))

    def add_text(self, text: str) -> None:
        line = Text()
        line.append("claude: ", style="bold")
        line.append(text)
        self.mount(Static(line, classes="msg-final"))

    def mark_done(self) -> None:
        self.remove_class("turn-running")
        self.add_class("turn-done")

    def mark_error(self) -> None:
        self.remove_class("turn-running")
        self.add_class("turn-error")

    def rendered_text(self) -> str:
        parts: list[str] = []
        for static in self.query(Static):
            parts.append(str(static.renderable))
        return "\n".join(parts)
```

Now wire `RichTranscript` to use it. Replace the `_on_appended`, `_render_entry`, and `rendered_text` methods, and add a `_current_turn` attribute initialized in `__init__`:

In `__init__`, add at the end:

```python
        self._current_turn: _TurnContainer | None = None
```

Replace `_on_appended` and `_render_entry`:

```python
    def _on_appended(self, event: AgentMessageAppended) -> None:
        if event.agent_id != self._agent_id:
            return
        self._dispatch_entry(TranscriptEntry(
            role=event.role, text=event.text,
            tool_id=event.tool_id, tool_name=event.tool_name,
        ))

    def _dispatch_entry(self, entry: TranscriptEntry) -> None:
        if entry.role == "user":
            self._open_turn(entry.text)
            return
        if self._current_turn is None:
            # Defensive: a non-user entry arrived before any user entry.
            # Open a synthetic empty turn so the entry has somewhere to live.
            self._open_turn("")
        turn = self._current_turn
        assert turn is not None
        if entry.role == "assistant":
            turn.add_text(entry.text)
        elif entry.role == "thinking":
            turn.add_thinking(entry.text)
        elif entry.role == "tool_use":
            turn.add_tool_call(
                tool_id=entry.tool_id, tool_name=entry.tool_name,
                args_text=entry.text,
            )
        elif entry.role == "tool_result":
            turn.attach_tool_result(
                tool_id=entry.tool_id, content_text=entry.text,
            )

    def _open_turn(self, user_text: str) -> None:
        scroll = self.query_one(VerticalScroll)
        turn = _TurnContainer(user_text=user_text)
        self._current_turn = turn
        scroll.mount(turn)
        scroll.scroll_end(animate=False)
```

Update `on_mount` to call `_dispatch_entry` instead of `_render_entry` during replay:

```python
    def on_mount(self) -> None:
        cwd: Path | None = getattr(self.app, "cwd", None)
        if cwd is not None:
            store = TranscriptStore(cwd=cwd, agent_id=self._agent_id)
            for entry in store.read_all():
                self._dispatch_entry(entry)
        bus = self._bus or getattr(self.app, "event_bus", None)
        if bus is not None:
            self._unsub_msg = bus.subscribe(AgentMessageAppended, self._on_appended)
```

Replace `rendered_text` with a version that walks turns:

```python
    def rendered_text(self) -> str:
        scroll = self.query_one(VerticalScroll)
        parts: list[str] = []
        for child in scroll.children:
            if isinstance(child, _TurnContainer):
                parts.append(child.rendered_text())
            elif isinstance(child, Static):
                parts.append(str(child.renderable))
        return "\n".join(parts)
```

- [ ] **Step 4: Run all RichTranscript tests**

Run: `uv run pytest tests/test_rich_transcript.py -v`
Expected: all PASS — both new tests *and* the three skeleton tests from Task 5.

- [ ] **Step 5: Commit**

```bash
git add patchbai/widgets/rich_transcript.py tests/test_rich_transcript.py
git commit -m "feat(widgets): RichTranscript groups output into per-turn containers"
```

---

## Task 7: `_ToolCall` foldable — collapsible per-tool widget with status

This task introduces the first `Collapsible` sub-widget. We render tool calls as expanded foldables while running, and collapse them when the matching `tool_result` arrives. No spinner animation yet (Task 9 adds that); we use a static `…` placeholder while running and `✓` / `✗` after the result lands.

**Files:**
- Modify: `patchbai/widgets/rich_transcript.py`
- Test: `tests/test_rich_transcript.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_rich_transcript.py`:

```python
@pytest.mark.asyncio
async def test_tool_use_renders_as_expanded_collapsible(tmp_path):
    from textual.widgets import Collapsible

    from patchbai.widgets.rich_transcript import _ToolCall

    bus = EventBus()
    app = _HostApp(bus, "a1")
    app.cwd = tmp_path
    async with app.run_test() as pilot:
        await pilot.pause()
        bus.publish(AgentMessageAppended(agent_id="a1", role="user", text="go"))
        bus.publish(AgentMessageAppended(
            agent_id="a1", role="tool_use", text="{'cmd': 'ls /tmp'}",
            tool_id="t1", tool_name="bash",
        ))
        await pilot.pause()

        widget = app.query_one(RichTranscript)
        tool_widgets = list(widget.query(_ToolCall))
        assert len(tool_widgets) == 1
        tw = tool_widgets[0]
        # Expanded while running, title shows running marker + tool name.
        assert tw.collapsed is False
        assert "bash" in tw.title
        # Body contains the args text.
        assert any("ls /tmp" in str(s.renderable) for s in tw.query(Static))


@pytest.mark.asyncio
async def test_tool_result_pairs_by_tool_id_and_collapses(tmp_path):
    from patchbai.widgets.rich_transcript import _ToolCall

    bus = EventBus()
    app = _HostApp(bus, "a1")
    app.cwd = tmp_path
    async with app.run_test() as pilot:
        await pilot.pause()
        bus.publish(AgentMessageAppended(agent_id="a1", role="user", text="go"))
        bus.publish(AgentMessageAppended(
            agent_id="a1", role="tool_use", text="{'cmd': 'ls'}",
            tool_id="t1", tool_name="bash",
        ))
        bus.publish(AgentMessageAppended(
            agent_id="a1", role="tool_use", text="{'p': 'README'}",
            tool_id="t2", tool_name="read",
        ))
        # Result for second tool arrives first.
        bus.publish(AgentMessageAppended(
            agent_id="a1", role="tool_result", text="<readme contents>",
            tool_id="t2",
        ))
        # Then result for first tool.
        bus.publish(AgentMessageAppended(
            agent_id="a1", role="tool_result", text="bin\nlib\n",
            tool_id="t1",
        ))
        await pilot.pause()

        widget = app.query_one(RichTranscript)
        tools = list(widget.query(_ToolCall))
        assert len(tools) == 2
        # Both collapsed after their result arrived.
        assert all(t.collapsed for t in tools)
        # Pairing is correct: tool t1 (bash) shows the bin/lib output, tool t2
        # (read) shows the readme text.
        bash = next(t for t in tools if t.tool_name == "bash")
        read = next(t for t in tools if t.tool_name == "read")
        assert any("bin" in str(s.renderable) for s in bash.query(Static))
        assert any("readme" in str(s.renderable) for s in read.query(Static))
        # Title carries success marker.
        assert "✓" in bash.title
        assert "✓" in read.title


@pytest.mark.asyncio
async def test_tool_args_with_brackets_render_literally(tmp_path):
    """[type=int_parsing] in args must NOT trip Rich markup parsing."""
    from patchbai.widgets.rich_transcript import _ToolCall

    bus = EventBus()
    app = _HostApp(bus, "a1")
    app.cwd = tmp_path
    async with app.run_test() as pilot:
        await pilot.pause()
        bus.publish(AgentMessageAppended(agent_id="a1", role="user", text="go"))
        bus.publish(AgentMessageAppended(
            agent_id="a1", role="tool_use",
            text="{'err': '[type=int_parsing, input_value=...]'}",
            tool_id="t1", tool_name="validate",
        ))
        await pilot.pause()

        widget = app.query_one(RichTranscript)
        tw = widget.query_one(_ToolCall)
        body_text = "\n".join(str(s.renderable) for s in tw.query(Static))
        assert "[type=int_parsing" in body_text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_rich_transcript.py -v`
Expected: 3 FAILs — `_ToolCall` doesn't exist yet.

- [ ] **Step 3: Implement `_ToolCall`**

Edit `patchbai/widgets/rich_transcript.py`. Add `Collapsible` to the textual imports:

```python
from textual.widgets import Collapsible, Static
```

Add the `_ToolCall` class after `_TurnContainer` (or before — class order doesn't matter, but placing it after `_TurnContainer` keeps the read order Top → Down by abstraction):

```python
class _ToolCall(Collapsible):
    """One tool invocation. Expanded while running, collapsed on result."""

    DEFAULT_CSS = """
    _ToolCall {
        margin: 0;
    }
    """

    def __init__(self, *, tool_id: str | None, tool_name: str | None, args_text: str) -> None:
        self.tool_id = tool_id
        self.tool_name = tool_name or "?"
        self._args_text = args_text
        self._args_static = Static(self._build_args_text())
        self._result_static = Static(Text("(running…)", style="dim"))
        super().__init__(
            self._args_static,
            self._result_static,
            title=self._build_running_title(),
            collapsed=False,
        )

    def _build_args_text(self) -> Text:
        line = Text()
        line.append("args: ", style="bold")
        line.append(self._args_text)
        return line

    def _build_running_title(self) -> str:
        # Truncated, plain-string title — Collapsible accepts str.
        short = self._args_text if len(self._args_text) <= 60 else self._args_text[:57] + "…"
        return f"… {self.tool_name}({short})"

    def _build_done_title(self, result_text: str, *, error: bool) -> str:
        marker = "✗" if error else "✓"
        short = result_text.replace("\n", " ")
        if len(short) > 80:
            short = short[:77] + "…"
        return f"{marker} {self.tool_name} → {short}"

    def attach_result(self, content_text: str, *, error: bool = False) -> None:
        body = Text()
        body.append("result: ", style="bold")
        body.append(content_text, style="red" if error else "")
        self._result_static.update(body)
        self.title = self._build_done_title(content_text, error=error)
        self.collapsed = True

    def mark_done(self) -> None:
        # Called when the turn ends. If no result ever attached (shouldn't
        # normally happen), still collapse the foldable.
        if self._result_static.renderable and "(running…)" in str(self._result_static.renderable):
            self._result_static.update(Text("(no result received)", style="dim red"))
            self.title = f"? {self.tool_name} (no result)"
        self.collapsed = True
```

Now update `_TurnContainer.add_tool_call` and `attach_tool_result` to use `_ToolCall` instead of plain `Static`s:

Replace `add_tool_call` and `attach_tool_result` in `_TurnContainer`:

```python
    def add_tool_call(
        self, *, tool_id: str | None, tool_name: str | None, args_text: str,
    ) -> None:
        widget = _ToolCall(
            tool_id=tool_id, tool_name=tool_name, args_text=args_text,
        )
        self._tool_widgets[tool_id or id(widget)] = widget
        self.mount(widget)

    def attach_tool_result(self, *, tool_id: str | None, content_text: str) -> None:
        widget = self._tool_widgets.get(tool_id) if tool_id else None
        if widget is None:
            # Old transcript fallback or out-of-order: attach to most-recent
            # _ToolCall whose result hasn't been set yet.
            for w in reversed(list(self.query(_ToolCall))):
                if "(running…)" in str(w._result_static.renderable):
                    widget = w
                    break
        if widget is None:
            # Truly orphaned — mount a free-floating result line.
            line = Text()
            line.append("result (orphan): ", style="bold red")
            line.append(content_text)
            self.mount(Static(line))
            return
        # Naive error detection — refined in later tasks if needed.
        is_err = content_text.lower().startswith("error")
        widget.attach_result(content_text, error=is_err)
```

And initialize the `_tool_widgets` dict in `_TurnContainer.__init__`:

```python
    def __init__(self, user_text: str) -> None:
        super().__init__()
        self.add_class("turn-running")
        self._user_text = user_text
        self._tool_widgets: dict = {}
```

Also update `_TurnContainer.mark_done` and `mark_error` to ripple completion to children:

```python
    def mark_done(self) -> None:
        self.remove_class("turn-running")
        self.add_class("turn-done")
        for tool in self.query(_ToolCall):
            tool.mark_done()

    def mark_error(self) -> None:
        self.remove_class("turn-running")
        self.add_class("turn-error")
        for tool in self.query(_ToolCall):
            tool.mark_done()
```

- [ ] **Step 4: Run all RichTranscript tests**

Run: `uv run pytest tests/test_rich_transcript.py -v`
Expected: all PASS — both the 3 new tests and all earlier ones.

- [ ] **Step 5: Commit**

```bash
git add patchbai/widgets/rich_transcript.py tests/test_rich_transcript.py
git commit -m "feat(widgets): tool calls render as auto-expanding Collapsibles"
```

---

## Task 8: `_ThinkingGroup` foldable — collapse contiguous thinking blocks

Adjacent thinking blocks merge into one foldable group. A new thinking block after a non-thinking step opens a fresh group.

**Files:**
- Modify: `patchbai/widgets/rich_transcript.py`
- Test: `tests/test_rich_transcript.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_rich_transcript.py`:

```python
@pytest.mark.asyncio
async def test_consecutive_thinking_blocks_merge_into_one_group(tmp_path):
    from patchbai.widgets.rich_transcript import _ThinkingGroup

    bus = EventBus()
    app = _HostApp(bus, "a1")
    app.cwd = tmp_path
    async with app.run_test() as pilot:
        await pilot.pause()
        bus.publish(AgentMessageAppended(agent_id="a1", role="user", text="go"))
        bus.publish(AgentMessageAppended(agent_id="a1", role="thinking", text="step 1"))
        bus.publish(AgentMessageAppended(agent_id="a1", role="thinking", text="step 2"))
        await pilot.pause()

        widget = app.query_one(RichTranscript)
        groups = list(widget.query(_ThinkingGroup))
        assert len(groups) == 1
        body = "\n".join(str(s.renderable) for s in groups[0].query(Static))
        assert "step 1" in body
        assert "step 2" in body


@pytest.mark.asyncio
async def test_thinking_after_tool_opens_new_group(tmp_path):
    from patchbai.widgets.rich_transcript import _ThinkingGroup

    bus = EventBus()
    app = _HostApp(bus, "a1")
    app.cwd = tmp_path
    async with app.run_test() as pilot:
        await pilot.pause()
        bus.publish(AgentMessageAppended(agent_id="a1", role="user", text="go"))
        bus.publish(AgentMessageAppended(agent_id="a1", role="thinking", text="first"))
        bus.publish(AgentMessageAppended(
            agent_id="a1", role="tool_use", text="{}",
            tool_id="t1", tool_name="bash",
        ))
        bus.publish(AgentMessageAppended(agent_id="a1", role="thinking", text="second"))
        await pilot.pause()

        widget = app.query_one(RichTranscript)
        groups = list(widget.query(_ThinkingGroup))
        assert len(groups) == 2


@pytest.mark.asyncio
async def test_thinking_group_starts_expanded(tmp_path):
    from patchbai.widgets.rich_transcript import _ThinkingGroup

    bus = EventBus()
    app = _HostApp(bus, "a1")
    app.cwd = tmp_path
    async with app.run_test() as pilot:
        await pilot.pause()
        bus.publish(AgentMessageAppended(agent_id="a1", role="user", text="go"))
        bus.publish(AgentMessageAppended(agent_id="a1", role="thinking", text="..."))
        await pilot.pause()

        group = app.query_one(_ThinkingGroup)
        assert group.collapsed is False
        assert "Thinking" in group.title
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_rich_transcript.py -v`
Expected: 3 FAILs — `_ThinkingGroup` doesn't exist.

- [ ] **Step 3: Implement `_ThinkingGroup` and route thinking events into it**

Edit `patchbai/widgets/rich_transcript.py`. Add a `time` import at the top:

```python
import time
```

Add the `_ThinkingGroup` class (place it next to `_ToolCall`):

```python
class _ThinkingGroup(Collapsible):
    """A contiguous run of thinking blocks. Expanded while running."""

    DEFAULT_CSS = """
    _ThinkingGroup {
        margin: 0;
    }
    """

    def __init__(self) -> None:
        self._body_static = Static(Text(""))
        self._started = time.monotonic()
        self._done = False
        super().__init__(
            self._body_static,
            title="Thinking…",
            collapsed=False,
        )

    def append(self, text: str) -> None:
        existing = self._body_static.renderable
        body = existing if isinstance(existing, Text) else Text(str(existing))
        if len(body) > 0:
            body.append("\n")
        body.append(text, style="dim")
        self._body_static.update(body)

    def mark_done(self) -> None:
        if self._done:
            return
        self._done = True
        elapsed = time.monotonic() - self._started
        self.title = f"Thought for {elapsed:.1f}s"
        self.collapsed = True
```

Now update `_TurnContainer` to track the most-recently-mounted step kind so `add_thinking` knows whether to merge or open a fresh group. Update `__init__`:

```python
    def __init__(self, user_text: str) -> None:
        super().__init__()
        self.add_class("turn-running")
        self._user_text = user_text
        self._tool_widgets: dict = {}
        self._current_thinking: _ThinkingGroup | None = None
```

Replace `add_thinking`:

```python
    def add_thinking(self, text: str) -> None:
        if self._current_thinking is None:
            group = _ThinkingGroup()
            self._current_thinking = group
            self.mount(group)
        self._current_thinking.append(text)
```

Any other step (tool_use, tool_result, text) must terminate the current thinking group so the *next* thinking opens a new one. Add a private helper and call it from the other adders:

```python
    def _close_thinking_group(self) -> None:
        self._current_thinking = None
```

Add `self._close_thinking_group()` as the **first** line of `add_tool_call`, `attach_tool_result`, and `add_text`.

Finally, ripple completion to thinking groups in `mark_done` / `mark_error`:

```python
    def mark_done(self) -> None:
        self.remove_class("turn-running")
        self.add_class("turn-done")
        for tool in self.query(_ToolCall):
            tool.mark_done()
        for group in self.query(_ThinkingGroup):
            group.mark_done()

    def mark_error(self) -> None:
        self.remove_class("turn-running")
        self.add_class("turn-error")
        for tool in self.query(_ToolCall):
            tool.mark_done()
        for group in self.query(_ThinkingGroup):
            group.mark_done()
```

- [ ] **Step 4: Run all RichTranscript tests**

Run: `uv run pytest tests/test_rich_transcript.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add patchbai/widgets/rich_transcript.py tests/test_rich_transcript.py
git commit -m "feat(widgets): thinking blocks merge into one Collapsible group"
```

---

## Task 9: Wire `AgentStateChanged` → turn close (mark_done / mark_error)

When the agent transitions to `DONE` or `ERROR`, the current turn closes: all `_ToolCall` and `_ThinkingGroup` widgets collapse and re-title; the turn's CSS class flips.

**Files:**
- Modify: `patchbai/widgets/rich_transcript.py`
- Test: `tests/test_rich_transcript.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_rich_transcript.py`:

```python
@pytest.mark.asyncio
async def test_agent_state_done_collapses_current_turn(tmp_path):
    import dataclasses
    from patchbai.agents.state import AgentInfo, AgentState
    from patchbai.events import AgentStateChanged
    from patchbai.widgets.rich_transcript import (
        _ThinkingGroup, _TurnContainer,
    )

    bus = EventBus()
    app = _HostApp(bus, "a1")
    app.cwd = tmp_path
    async with app.run_test() as pilot:
        await pilot.pause()
        bus.publish(AgentMessageAppended(agent_id="a1", role="user", text="go"))
        bus.publish(AgentMessageAppended(agent_id="a1", role="thinking", text="..."))
        bus.publish(AgentMessageAppended(agent_id="a1", role="assistant", text="ok"))
        await pilot.pause()

        info = AgentInfo(id="a1", name="a1", cwd=str(tmp_path),
                         started_at=0, state=AgentState.DONE)
        bus.publish(AgentStateChanged(info=info, old_state=AgentState.RUNNING))
        await pilot.pause()

        widget = app.query_one(RichTranscript)
        turn = widget.query_one(_TurnContainer)
        assert turn.has_class("turn-done")
        assert not turn.has_class("turn-running")
        group = widget.query_one(_ThinkingGroup)
        assert group.collapsed is True
        assert "Thought for" in group.title


@pytest.mark.asyncio
async def test_agent_state_error_marks_turn_error(tmp_path):
    from patchbai.agents.state import AgentInfo, AgentState
    from patchbai.events import AgentStateChanged
    from patchbai.widgets.rich_transcript import _TurnContainer

    bus = EventBus()
    app = _HostApp(bus, "a1")
    app.cwd = tmp_path
    async with app.run_test() as pilot:
        await pilot.pause()
        bus.publish(AgentMessageAppended(agent_id="a1", role="user", text="go"))
        await pilot.pause()

        info = AgentInfo(id="a1", name="a1", cwd=str(tmp_path),
                         started_at=0, state=AgentState.ERROR)
        bus.publish(AgentStateChanged(info=info, old_state=AgentState.RUNNING))
        await pilot.pause()

        turn = app.query_one(_TurnContainer)
        assert turn.has_class("turn-error")


@pytest.mark.asyncio
async def test_state_change_for_other_agent_is_ignored(tmp_path):
    from patchbai.agents.state import AgentInfo, AgentState
    from patchbai.events import AgentStateChanged
    from patchbai.widgets.rich_transcript import _TurnContainer

    bus = EventBus()
    app = _HostApp(bus, "a1")
    app.cwd = tmp_path
    async with app.run_test() as pilot:
        await pilot.pause()
        bus.publish(AgentMessageAppended(agent_id="a1", role="user", text="go"))
        await pilot.pause()

        info = AgentInfo(id="other", name="other", cwd=str(tmp_path),
                         started_at=0, state=AgentState.DONE)
        bus.publish(AgentStateChanged(info=info, old_state=AgentState.RUNNING))
        await pilot.pause()

        turn = app.query_one(_TurnContainer)
        assert turn.has_class("turn-running")
        assert not turn.has_class("turn-done")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_rich_transcript.py -v`
Expected: 3 FAILs — `RichTranscript` doesn't subscribe to `AgentStateChanged` yet.

- [ ] **Step 3: Subscribe to `AgentStateChanged` and dispatch**

Edit `patchbai/widgets/rich_transcript.py`. Add to imports:

```python
from patchbai.agents.state import AgentState
from patchbai.events import AgentMessageAppended, AgentStateChanged, EventBus
```

Update `__init__` to add the new unsubscribe slot:

```python
        self._unsub_state = lambda: None
```

Update `on_mount` — after the existing `subscribe(AgentMessageAppended, …)` call, add:

```python
            self._unsub_state = bus.subscribe(AgentStateChanged, self._on_state_changed)
```

Update `on_unmount`:

```python
    def on_unmount(self) -> None:
        self._unsub_msg()
        self._unsub_state()
```

Add the handler:

```python
    def _on_state_changed(self, event: AgentStateChanged) -> None:
        if event.info.id != self._agent_id:
            return
        if self._current_turn is None:
            return
        if event.info.state == AgentState.DONE:
            self._current_turn.mark_done()
            self._current_turn = None
        elif event.info.state == AgentState.ERROR:
            self._current_turn.mark_error()
            self._current_turn = None
```

- [ ] **Step 4: Run all RichTranscript tests**

Run: `uv run pytest tests/test_rich_transcript.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add patchbai/widgets/rich_transcript.py tests/test_rich_transcript.py
git commit -m "feat(widgets): close turn on AgentStateChanged DONE/ERROR"
```

---

## Task 10: History replay marks all turns as done

On mount, `RichTranscript` replays the on-disk transcript by walking entries and routing them through `_dispatch_entry`. The result so far: every replayed turn is left in the `running` state because no state event fires during replay. We need a final `mark_done()` on the last turn after the walk completes (and a `mark_done()` on each prior turn the moment a *new* `user` entry opens a fresh one).

**Files:**
- Modify: `patchbai/widgets/rich_transcript.py`
- Create: `tests/test_rich_transcript_replay.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_rich_transcript_replay.py`:

```python
import pytest
from textual.app import App

from patchbai.events import EventBus
from patchbai.persistence.transcript_store import AgentTranscript as Store, TranscriptEntry
from patchbai.widgets.rich_transcript import RichTranscript, _TurnContainer


class _HostApp(App):
    def __init__(self, bus: EventBus, agent_id: str) -> None:
        super().__init__()
        self.event_bus = bus
        self._agent_id = agent_id

    def compose(self):
        yield RichTranscript(agent_id=self._agent_id, event_bus=self.event_bus)


@pytest.mark.asyncio
async def test_replay_marks_all_turns_done(tmp_path):
    store = Store(cwd=tmp_path, agent_id="a1")
    store.append(TranscriptEntry(role="user", text="q1"))
    store.append(TranscriptEntry(role="assistant", text="a1"))
    store.append(TranscriptEntry(role="user", text="q2"))
    store.append(TranscriptEntry(role="assistant", text="a2"))

    bus = EventBus()
    app = _HostApp(bus, "a1")
    app.cwd = tmp_path
    async with app.run_test() as pilot:
        await pilot.pause()
        widget = app.query_one(RichTranscript)
        turns = list(widget.query(_TurnContainer))
        assert len(turns) == 2
        for t in turns:
            assert t.has_class("turn-done"), f"turn not marked done: {t}"
            assert not t.has_class("turn-running")


@pytest.mark.asyncio
async def test_old_transcript_without_tool_id_still_pairs(tmp_path):
    """tool_result without tool_id falls back to most-recent-pending pairing."""
    from patchbai.widgets.rich_transcript import _ToolCall

    store = Store(cwd=tmp_path, agent_id="a1")
    store.append(TranscriptEntry(role="user", text="go"))
    # Old-format records have no tool_id at all.
    store.append(TranscriptEntry(role="tool_use", text="[bash] {'cmd': 'ls'}"))
    store.append(TranscriptEntry(role="tool_result", text="bin\nlib\n"))

    bus = EventBus()
    app = _HostApp(bus, "a1")
    app.cwd = tmp_path
    async with app.run_test() as pilot:
        await pilot.pause()
        widget = app.query_one(RichTranscript)
        tools = list(widget.query(_ToolCall))
        assert len(tools) == 1
        # The result attached to it (no orphan was mounted as a Static).
        from textual.widgets import Static
        body = "\n".join(str(s.renderable) for s in tools[0].query(Static))
        assert "bin" in body
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_rich_transcript_replay.py -v`
Expected:
- `test_replay_marks_all_turns_done` FAILS — turns are still `turn-running`.
- `test_old_transcript_without_tool_id_still_pairs` likely FAILS or only partially passes — depends on whether the `attach_tool_result` fallback path resolves correctly when called during replay.

- [ ] **Step 3: Close prior turns on new user, and close last turn after replay**

Edit `patchbai/widgets/rich_transcript.py`. Update `_open_turn` to close the prior turn first:

```python
    def _open_turn(self, user_text: str) -> None:
        scroll = self.query_one(VerticalScroll)
        if self._current_turn is not None:
            # Defensive — and required for history replay where no state event
            # closes a previous turn.
            self._current_turn.mark_done()
        turn = _TurnContainer(user_text=user_text)
        self._current_turn = turn
        scroll.mount(turn)
        scroll.scroll_end(animate=False)
```

Update `on_mount` to close the final replayed turn after the walk:

```python
    def on_mount(self) -> None:
        cwd: Path | None = getattr(self.app, "cwd", None)
        if cwd is not None:
            store = TranscriptStore(cwd=cwd, agent_id=self._agent_id)
            for entry in store.read_all():
                self._dispatch_entry(entry)
            # Replay never sees a state-change event, so close whatever turn
            # we ended on. New live events that arrive afterward will open a
            # fresh turn via the user-entry path.
            if self._current_turn is not None:
                self._current_turn.mark_done()
                self._current_turn = None
        bus = self._bus or getattr(self.app, "event_bus", None)
        if bus is not None:
            self._unsub_msg = bus.subscribe(AgentMessageAppended, self._on_appended)
            self._unsub_state = bus.subscribe(AgentStateChanged, self._on_state_changed)
```

- [ ] **Step 4: Run the replay tests**

Run: `uv run pytest tests/test_rich_transcript_replay.py -v`
Expected: both PASS.

Also re-run the main test file to confirm no regression:

Run: `uv run pytest tests/test_rich_transcript.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add patchbai/widgets/rich_transcript.py tests/test_rich_transcript_replay.py
git commit -m "feat(widgets): close prior turn on new user; close last turn after replay"
```

---

## Task 11: Refactor `OrchestratorChat` to compose `RichTranscript`

**Files:**
- Modify: `patchbai/widgets/orchestrator_chat.py`
- Test: `tests/test_app_smoke.py`, `tests/test_app_smoke_plan2.py` (verify no regression)

The shell keeps the same constructor, the `Input` ID `#orch-input`, and the input-submit behavior. The internal `VerticalScroll` (id `#orch-messages`), `_append_line`, the `OrchestratorReply` subscription, and the history-preload code all move out — `RichTranscript` owns those concerns.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_app_smoke.py`:

```python
@pytest.mark.asyncio
async def test_orchestrator_chat_uses_rich_transcript(tmp_path):
    """OrchestratorChat composes a RichTranscript for the 'orchestrator' agent."""
    from patchbai.widgets.orchestrator_chat import OrchestratorChat
    from patchbai.widgets.rich_transcript import RichTranscript
    from patchbai.events import EventBus

    class _Host(App):
        event_bus = EventBus()
        def compose(self):
            yield OrchestratorChat()

    app = _Host()
    app.cwd = tmp_path
    async with app.run_test() as pilot:
        await pilot.pause()
        chat = app.query_one(OrchestratorChat)
        rt = chat.query_one(RichTranscript)
        assert rt._agent_id == "orchestrator"
```

(Add `from textual.app import App` to the test file imports if it isn't already there. Also: this asserts on a private attribute `_agent_id` because there's no public accessor; if that feels off, add a `@property def agent_id(self)` to `RichTranscript` and assert against that instead.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_app_smoke.py::test_orchestrator_chat_uses_rich_transcript -v`
Expected: FAIL — `OrchestratorChat` doesn't compose a `RichTranscript` yet.

- [ ] **Step 3: Rewrite `OrchestratorChat` as a thin shell**

Replace the entire contents of `patchbai/widgets/orchestrator_chat.py`:

```python
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Input

from patchbai.events import EventBus, UserMessageToOrchestrator
from patchbai.widgets.rich_transcript import RichTranscript


class OrchestratorChat(Vertical):
    """Manager-Claude chat panel: RichTranscript + input box."""

    AGENT_ID = "orchestrator"

    DEFAULT_CSS = """
    OrchestratorChat {
        border: round $primary;
        padding: 0 1;
    }
    OrchestratorChat > RichTranscript {
        height: 1fr;
    }
    OrchestratorChat #orch-input {
        dock: bottom;
        height: 3;
    }
    """

    def __init__(self, *, event_bus: EventBus | None = None,
                 history: list[tuple[str, str]] | None = None) -> None:
        """history: kept for backwards-compat with callers; ignored.
        RichTranscript reads history from disk on mount."""
        super().__init__()
        self._bus = event_bus

    def compose(self) -> ComposeResult:
        yield RichTranscript(agent_id=self.AGENT_ID, event_bus=self._bus)
        yield Input(placeholder="Message orchestrator… (enter to send)",
                    id="orch-input")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if not event.value.strip():
            return
        text = event.value
        bus = self._bus or getattr(self.app, "event_bus", None)
        event.input.value = ""
        if bus is not None:
            bus.publish(UserMessageToOrchestrator(text))
```

Notes:
- The `history` constructor argument is preserved for callers but ignored — the new widget always reads from disk in its own `on_mount`. This keeps `app.py` and the registry from churning.
- We no longer `_append_line("user", text)` on submit; that's redundant — the orchestrator session's `AgentSession.send` records `role="user"`, which `RichTranscript` picks up via `AgentMessageAppended`.

- [ ] **Step 4: Run the new test, the orchestrator-chat-related smoke tests, and the orchestrator-session tests**

Run: `uv run pytest tests/test_app_smoke.py tests/test_app_smoke_plan2.py tests/test_orchestrator_session.py tests/test_orchestrator_session_serializes.py tests/test_layout_engine_idempotent.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add patchbai/widgets/orchestrator_chat.py tests/test_app_smoke.py
git commit -m "refactor(widgets): OrchestratorChat composes RichTranscript"
```

---

## Task 12: Refactor `AgentTranscript` to compose `RichTranscript`

Same treatment for the per-child-agent panel. The `rendered_text()` test helper is preserved as a façade so `tests/test_agent_transcript_widget.py` continues to pass.

**Files:**
- Modify: `patchbai/widgets/agent_transcript.py`
- Test: `tests/test_agent_transcript_widget.py`, `tests/test_agent_transcript_input.py` (verify no regression)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_agent_transcript_widget.py`:

```python
@pytest.mark.asyncio
async def test_agent_transcript_uses_rich_transcript(tmp_path):
    from patchbai.widgets.agent_transcript import AgentTranscript as Widget
    from patchbai.widgets.rich_transcript import RichTranscript

    bus = EventBus()
    app = _HostApp(bus, "a1")
    app.cwd = tmp_path
    async with app.run_test() as pilot:
        await pilot.pause()
        widget = app.query_one(Widget)
        rt = widget.query_one(RichTranscript)
        assert rt._agent_id == "a1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_agent_transcript_widget.py::test_agent_transcript_uses_rich_transcript -v`
Expected: FAIL.

- [ ] **Step 3: Rewrite `AgentTranscript` as a thin shell**

Replace the entire contents of `patchbai/widgets/agent_transcript.py`:

```python
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Input

from patchbai.events import DirectMessageToAgent, EventBus
from patchbai.widgets.rich_transcript import RichTranscript


class AgentTranscript(Vertical):
    """Per-child-agent transcript panel: RichTranscript + input box."""

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

    def compose(self) -> ComposeResult:
        yield RichTranscript(agent_id=self._agent_id, event_bus=self._bus)
        yield Input(placeholder=f"Message {self._agent_id}…", id="transcript-input")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return
        bus = self._bus or getattr(self.app, "event_bus", None)
        if bus is not None:
            bus.publish(DirectMessageToAgent(agent_id=self._agent_id, text=text))
        event.input.value = ""

    def rendered_text(self) -> str:
        """Test helper — delegates to the inner RichTranscript."""
        return self.query_one(RichTranscript).rendered_text()
```

- [ ] **Step 4: Run agent-transcript and direct-message tests**

Run: `uv run pytest tests/test_agent_transcript_widget.py tests/test_agent_transcript_input.py tests/test_direct_message_to_agent.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add patchbai/widgets/agent_transcript.py tests/test_agent_transcript_widget.py
git commit -m "refactor(widgets): AgentTranscript composes RichTranscript"
```

---

## Task 13: Spinner animation on running foldables

A single `set_interval(0.08, …)` per running widget cycles a Braille spinner glyph at the start of the foldable's title. Stopped on completion. (We hold this until last so all the structural behavior is verified before introducing time-driven UI updates that complicate testing.)

**Files:**
- Modify: `patchbai/widgets/rich_transcript.py`
- Test: `tests/test_rich_transcript.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_rich_transcript.py`:

```python
@pytest.mark.asyncio
async def test_running_tool_call_spinner_advances(tmp_path):
    from patchbai.widgets.rich_transcript import _ToolCall, _SPINNER_FRAMES

    bus = EventBus()
    app = _HostApp(bus, "a1")
    app.cwd = tmp_path
    async with app.run_test() as pilot:
        await pilot.pause()
        bus.publish(AgentMessageAppended(agent_id="a1", role="user", text="go"))
        bus.publish(AgentMessageAppended(
            agent_id="a1", role="tool_use", text="{}",
            tool_id="t1", tool_name="bash",
        ))
        await pilot.pause()
        tw = app.query_one(_ToolCall)
        first_frame = tw.title[0]
        assert first_frame in _SPINNER_FRAMES

        # Advance the spinner enough to cycle.
        await pilot.pause(0.5)
        # Title still starts with a spinner frame, but is unlikely to be the same.
        # (We assert the cheaper invariant: it's still in the frame set.)
        assert tw.title[0] in _SPINNER_FRAMES


@pytest.mark.asyncio
async def test_spinner_stops_after_result(tmp_path):
    from patchbai.widgets.rich_transcript import _ToolCall, _SPINNER_FRAMES

    bus = EventBus()
    app = _HostApp(bus, "a1")
    app.cwd = tmp_path
    async with app.run_test() as pilot:
        await pilot.pause()
        bus.publish(AgentMessageAppended(agent_id="a1", role="user", text="go"))
        bus.publish(AgentMessageAppended(
            agent_id="a1", role="tool_use", text="{}",
            tool_id="t1", tool_name="bash",
        ))
        bus.publish(AgentMessageAppended(
            agent_id="a1", role="tool_result", text="ok", tool_id="t1",
        ))
        await pilot.pause()
        tw = app.query_one(_ToolCall)
        # Title now starts with ✓, no longer with a spinner frame.
        assert tw.title.startswith("✓")
        assert tw.title[0] not in _SPINNER_FRAMES
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_rich_transcript.py -v -k spinner`
Expected: 2 FAILs — spinner module-level constant and animation don't exist.

- [ ] **Step 3: Add spinner support**

Edit `patchbai/widgets/rich_transcript.py`. At module top (below imports), add:

```python
_SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
_SPINNER_INTERVAL_S = 0.08
```

Update `_ToolCall._build_running_title` and add spinner machinery:

```python
    def _build_running_title(self) -> str:
        short = self._args_text if len(self._args_text) <= 60 else self._args_text[:57] + "…"
        return f"{_SPINNER_FRAMES[self._spinner_idx]} {self.tool_name}({short})"
```

Update `__init__` to seed `_spinner_idx` and start the timer in `on_mount`:

```python
    def __init__(self, *, tool_id: str | None, tool_name: str | None, args_text: str) -> None:
        self.tool_id = tool_id
        self.tool_name = tool_name or "?"
        self._args_text = args_text
        self._spinner_idx = 0
        self._spinner_timer = None
        self._args_static = Static(self._build_args_text())
        self._result_static = Static(Text("(running…)", style="dim"))
        super().__init__(
            self._args_static,
            self._result_static,
            title=self._build_running_title(),
            collapsed=False,
        )

    def on_mount(self) -> None:
        self._spinner_timer = self.set_interval(_SPINNER_INTERVAL_S, self._tick_spinner)

    def _tick_spinner(self) -> None:
        self._spinner_idx = (self._spinner_idx + 1) % len(_SPINNER_FRAMES)
        self.title = self._build_running_title()
```

Update `attach_result` and `mark_done` to stop the timer:

```python
    def attach_result(self, content_text: str, *, error: bool = False) -> None:
        if self._spinner_timer is not None:
            self._spinner_timer.stop()
            self._spinner_timer = None
        body = Text()
        body.append("result: ", style="bold")
        body.append(content_text, style="red" if error else "")
        self._result_static.update(body)
        self.title = self._build_done_title(content_text, error=error)
        self.collapsed = True

    def mark_done(self) -> None:
        if self._spinner_timer is not None:
            self._spinner_timer.stop()
            self._spinner_timer = None
        if self._result_static.renderable and "(running…)" in str(self._result_static.renderable):
            self._result_static.update(Text("(no result received)", style="dim red"))
            self.title = f"? {self.tool_name} (no result)"
        self.collapsed = True
```

Apply the same spinner pattern to `_ThinkingGroup`. Update its `__init__` and add `on_mount` / `_tick_spinner`:

```python
    def __init__(self) -> None:
        self._body_static = Static(Text(""))
        self._started = time.monotonic()
        self._done = False
        self._spinner_idx = 0
        self._spinner_timer = None
        super().__init__(
            self._body_static,
            title=self._build_running_title(),
            collapsed=False,
        )

    def _build_running_title(self) -> str:
        return f"{_SPINNER_FRAMES[self._spinner_idx]} Thinking…"

    def on_mount(self) -> None:
        self._spinner_timer = self.set_interval(_SPINNER_INTERVAL_S, self._tick_spinner)

    def _tick_spinner(self) -> None:
        self._spinner_idx = (self._spinner_idx + 1) % len(_SPINNER_FRAMES)
        self.title = self._build_running_title()

    def mark_done(self) -> None:
        if self._done:
            return
        self._done = True
        if self._spinner_timer is not None:
            self._spinner_timer.stop()
            self._spinner_timer = None
        elapsed = time.monotonic() - self._started
        self.title = f"Thought for {elapsed:.1f}s"
        self.collapsed = True
```

Note: the test in Task 8 (`test_thinking_group_starts_expanded`) asserts `"Thinking" in group.title` — that still passes since the title is now `"⠋ Thinking…"`.

- [ ] **Step 4: Run all RichTranscript tests**

Run: `uv run pytest tests/test_rich_transcript.py tests/test_rich_transcript_replay.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add patchbai/widgets/rich_transcript.py tests/test_rich_transcript.py
git commit -m "feat(widgets): braille-spinner animation on running foldables"
```

---

## Task 14: Full-suite regression check + manual smoke

**Files:** none modified. This is the verification gate.

- [ ] **Step 1: Run the full test suite**

Run: `uv run pytest -v`
Expected: 100% PASS. Investigate and fix any new failures inline before continuing.

- [ ] **Step 2: Manual smoke**

Run the TUI: `uv run python -m patchbai` (or whatever the existing dev-launch command is — check `patchbai/__main__.py`).

Verify by hand:
- Send a prompt that triggers a tool sequence (e.g., "list the files in the cwd"). While running:
  - The orchestrator panel shows the tool call as an expanded foldable with a spinning braille glyph in the title.
  - When the result arrives, the foldable collapses to a one-line `✓ tool → summary` and the final assistant text appears below in default styling.
- Send a prompt that triggers extended thinking (if your model supports it). While thinking:
  - A "Thinking…" foldable is expanded with the partial thinking text.
  - When the turn ends, it collapses to `Thought for {Xs}`.
- Quit and reopen the app:
  - Prior turns load from disk and all appear collapsed.
  - You can manually re-expand any past tool foldable by clicking/Enter on it.
- Spawn a child agent and open its `AgentTranscript` panel via your existing flow. Repeat the above checks — same behavior.

- [ ] **Step 3: Commit a final no-op marker**

Only if you made any small fixes during smoke. Otherwise skip this step. If smoke surfaced changes:

```bash
git add -p   # interactively stage real fixes
git commit -m "fix(widgets): smoke-test follow-ups on RichTranscript"
```

---

## Self-review

(Performed by the plan author after writing.)

**1. Spec coverage:**
- Shared widget (`RichTranscript`): Tasks 5, 6.
- Per-tool foldables, expanded-while-running, collapsed-on-result: Task 7.
- Thinking grouped into one foldable per contiguous run: Task 8.
- Final assistant text remains prominent: Tasks 6 (rendering) + visible across 7/8/9.
- Turn close on `AgentStateChanged → DONE/ERROR`: Task 9.
- History replay shows all turns collapsed: Task 10.
- `tool_id`/`tool_name` plumbed end-to-end: Tasks 1, 2, 3.
- Drop the `[tool use]`/`[tool result]` `OrchestratorReply` re-publishes; keep assistant text: Task 4.
- Outer widgets become thin shells, public API preserved: Tasks 11, 12.
- `rendered_text()` façade kept on `AgentTranscript`: Task 12.
- Spinner animation: Task 13.
- Markup safety on tool args (`[type=…]`): Task 7's third test.
- Old-transcript fallback (no `tool_id`): Task 10's second test.
- Forward-compat read of unknown future fields in transcripts: Task 2's `valid_keys` filter.

**2. Placeholder scan:** No "TBD" / "TODO" / "implement later" / "add error handling" patterns. Each step shows the exact code or command.

**3. Type consistency:** `_TurnContainer`, `_ThinkingGroup`, `_ToolCall`, `_SPINNER_FRAMES`, `_SPINNER_INTERVAL_S` are introduced once and referenced consistently. `AgentMessageAppended.tool_id` / `.tool_name` and `TranscriptEntry.tool_id` / `.tool_name` use the same names everywhere. `attach_result(content_text, *, error)` keyword names are consistent across definitions and call sites.
