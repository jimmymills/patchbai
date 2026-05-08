# Patchfeld Plan 3 — Full Agent Control Loop

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the bidirectional control loop between orchestrator and children. The orchestrator gets `send_to_agent` / `interrupt_agent` / `kill_agent` / `respond_to_agent_request` MCP tools. Every child gets `notify_orchestrator` and `ask_orchestrator` injected. The user can type directly to a child via the focused `AgentTranscript`'s new input box. Plus two carry-overs from plan 2's final review: serialize the orchestrator's `_on_user_message` create_task race behind a lock, and add an opt-in real-SDK smoke test gated on `ANTHROPIC_API_KEY`.

**Architecture:** A `RequestInbox` per child maps `request_id → asyncio.Future`. `ask_orchestrator` registers a future, publishes `AgentRequestedUserInput`, and awaits resolution (with timeout). The orchestrator's new `respond_to_agent_request` tool resolves the future. `notify_orchestrator` is fire-and-forget: it publishes `AgentNotifiedOrchestrator`, which the OrchestratorSession injects into its own session as a synthetic user message so the orchestrator AI can react in-flow. Direct user input flows through a new `DirectMessageToAgent` event from `AgentTranscript`'s input box → AgentManager → `AgentSession.send`.

**Tech Stack:** Python 3.11+, Textual, pydantic v2, `claude-agent-sdk`, pytest + pytest-asyncio.

**Non-goals for this plan (deferred to later plans):**
- `set_layout` runtime tool, save/load named layouts, History view, layout switcher (plan 4)
- `bind_key` / `set_config` / hot-reload + action registry (plan 4)
- Rich widget library: DiffViewer, FileTree, FileViewer, LogTail, Markdown, Notebook, Terminal/PTY (plan 5)
- Mode-C custom widgets (plan 6)
- A modal Textual permission UI replacing `bypassPermissions` (plan 4)
- Peer-to-peer messaging between child agents (out of scope for v1 entirely per spec)

---

## File Structure

```
patchfeld/
  agents/
    manager.py            (MODIFY: add send / get_inbox; subscribe to DirectMessageToAgent)
    session.py            (MODIFY: send() under an asyncio.Lock for safe re-entry)
    request_inbox.py      (NEW: RequestInbox — per-agent request_id → Future map)
    child_tools.py        (NEW: notify_orchestrator + ask_orchestrator MCP server)
  orchestrator/
    tools.py              (REFACTOR: extract _TOOL_SPECS; add send/interrupt/kill/respond)
    session.py            (MODIFY: serialize _on_user_message via asyncio.Lock; subscribe to AgentRequestedUserInput + AgentNotifiedOrchestrator and inject into its own stream)
  events.py               (EXTEND: AgentNotifiedOrchestrator, DirectMessageToAgent)
  widgets/
    agent_transcript.py   (MODIFY: add bottom Input; publish DirectMessageToAgent on submit)
tests/
  test_request_inbox.py
  test_child_tools.py
  test_agent_session_send_lock.py
  test_orchestrator_session_serializes.py
  test_orchestrator_tools_send_interrupt_kill.py
  test_orchestrator_tools_respond.py
  test_direct_message_to_agent.py
  test_agent_transcript_input.py
  test_app_smoke_plan3.py            (e2e: ask_orchestrator round-trip)
  test_real_sdk_smoke.py             (opt-in, gated on ANTHROPIC_API_KEY)
```

---

## Task 1 — Refactor `orchestrator/tools.py` to share tool schemas

**Files:**
- Modify: `patchfeld/orchestrator/tools.py`

The plan-2 dual-path approach (`build_orchestrator_tools` returns `.handler`s for tests; `build_orchestrator_mcp_server` re-decorates) duplicates each tool's name/description/schema. We extract a `_TOOL_SPECS` table so both paths consume the same source of truth, preventing silent drift as we add 4 more tools in this plan.

- [ ] **Step 1: Open `patchfeld/orchestrator/tools.py` and replace its contents with**

```python
import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from claude_agent_sdk import create_sdk_mcp_server, tool

from patchfeld.agents.manager import AgentManager


@dataclass(frozen=True)
class _ToolSpec:
    name: str
    description: str
    input_schema: dict
    # build(manager) returns the async handler for this tool
    build: Callable[[AgentManager], Callable[[dict], Awaitable[dict]]]


def _spawn_handler(manager: AgentManager):
    async def spawn_agent(args: dict) -> dict:
        agent_id = await manager.spawn(
            name=args["name"],
            prompt=args["prompt"],
            cwd=args.get("cwd"),
            allowed_tools=args.get("allowed_tools"),
        )
        return {
            "content": [
                {"type": "text", "text": f"Spawned agent {agent_id} ({args['name']})"}
            ]
        }
    return spawn_agent


def _list_handler(manager: AgentManager):
    async def list_agents(_args: dict) -> dict:
        infos = [info.to_dict() for info in manager.list_infos()]
        return {"content": [{"type": "text", "text": json.dumps(infos, indent=2)}]}
    return list_agents


def _read_handler(manager: AgentManager):
    async def read_agent_transcript(args: dict) -> dict:
        entries = manager.read_transcript(args["agent_id"])
        text = "\n".join(f"[{e.role}] {e.text}" for e in entries)
        return {"content": [{"type": "text", "text": text}]}
    return read_agent_transcript


_SPECS: list[_ToolSpec] = [
    _ToolSpec(
        name="spawn_agent",
        description=(
            "Spawn a new Claude Code child agent with the given name and "
            "initial prompt. Returns the agent id."
        ),
        input_schema={"name": str, "prompt": str},
        build=_spawn_handler,
    ),
    _ToolSpec(
        name="list_agents",
        description="List all currently registered agents and their states.",
        input_schema={},
        build=_list_handler,
    ),
    _ToolSpec(
        name="read_agent_transcript",
        description="Read the full transcript of an agent by id.",
        input_schema={"agent_id": str},
        build=_read_handler,
    ),
]


def build_orchestrator_tools(manager: AgentManager):
    """Return the bare async handlers (for unit testing)."""
    return tuple(spec.build(manager) for spec in _SPECS)


def build_orchestrator_mcp_server(manager: AgentManager):
    sdk_tools = []
    for spec in _SPECS:
        handler = spec.build(manager)
        decorated = tool(spec.name, spec.description, spec.input_schema)(handler)
        sdk_tools.append(decorated)
    return create_sdk_mcp_server(
        name="patchfeld_orchestrator",
        version="1.0.0",
        tools=sdk_tools,
    )
```

- [ ] **Step 2: Run the existing orchestrator tools tests to confirm no regression**

```bash
cd /Users/jimmy.mills/Developer/patchfeld
.venv/bin/pytest tests/test_orchestrator_tools.py -v
.venv/bin/pytest -q
```

Expected: all 3 plan-2 tool tests still pass; full suite still green (98).

If any test fails, the refactor changed behavior — STOP and report.

- [ ] **Step 3: Commit**

```bash
git add patchfeld/orchestrator/tools.py
git commit -m "refactor(orchestrator): share tool specs across handler + MCP-server paths"
```

---

## Task 2 — `RequestInbox`

**Files:**
- Create: `patchfeld/agents/request_inbox.py`
- Test: `tests/test_request_inbox.py`

`RequestInbox` is a small per-agent map of `request_id → asyncio.Future`. The child's `ask_orchestrator` tool registers a future and awaits it; the orchestrator's `respond_to_agent_request` tool resolves it. A timeout helper races the wait against `asyncio.sleep(timeout_s)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_request_inbox.py`:

```python
import asyncio

import pytest

from patchfeld.agents.request_inbox import RequestInbox


@pytest.mark.asyncio
async def test_register_and_resolve_round_trip():
    inbox = RequestInbox()
    request_id = inbox.register()

    async def resolver():
        await asyncio.sleep(0)
        inbox.resolve(request_id, "the answer")

    asyncio.create_task(resolver())
    result = await inbox.wait(request_id, timeout_s=1.0)
    assert result == "the answer"


@pytest.mark.asyncio
async def test_wait_times_out_when_no_resolution():
    inbox = RequestInbox()
    request_id = inbox.register()
    with pytest.raises(asyncio.TimeoutError):
        await inbox.wait(request_id, timeout_s=0.05)


@pytest.mark.asyncio
async def test_resolve_unknown_id_is_silently_ignored():
    inbox = RequestInbox()
    inbox.resolve("nonexistent", "ignored")  # must not raise


def test_register_returns_unique_ids():
    inbox = RequestInbox()
    a = inbox.register()
    b = inbox.register()
    assert a != b


@pytest.mark.asyncio
async def test_pending_returns_open_request_ids():
    inbox = RequestInbox()
    a = inbox.register()
    b = inbox.register()
    inbox.resolve(a, "done")
    await inbox.wait(a, timeout_s=1.0)  # drain
    assert inbox.pending() == [b]
```

- [ ] **Step 2: Run tests and confirm they fail**

```bash
.venv/bin/pytest tests/test_request_inbox.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement `patchfeld/agents/request_inbox.py`**

```python
import asyncio
import uuid


class RequestInbox:
    """Per-agent registry of pending ask_orchestrator requests.

    Each registered request_id has an asyncio.Future. The agent's tool call
    awaits the future (with a timeout); the orchestrator's reply resolves it.
    """

    def __init__(self) -> None:
        self._futures: dict[str, asyncio.Future] = {}

    def register(self) -> str:
        request_id = uuid.uuid4().hex[:12]
        loop = asyncio.get_event_loop()
        self._futures[request_id] = loop.create_future()
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

    def pending(self) -> list[str]:
        return [rid for rid, fut in self._futures.items() if not fut.done()]
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest tests/test_request_inbox.py -v
.venv/bin/pytest -q
```

Expected: 5 new pass; full suite green.

- [ ] **Step 5: Commit**

```bash
git add patchfeld/agents/request_inbox.py tests/test_request_inbox.py
git commit -m "feat(agents): RequestInbox for ask_orchestrator request/response correlation"
```

---

## Task 3 — Add `AgentNotifiedOrchestrator` + `DirectMessageToAgent` events

**Files:**
- Modify: `patchfeld/events.py`
- Test: `tests/test_agent_events.py` (extend with two tests)

- [ ] **Step 1: Append two tests to `tests/test_agent_events.py`**

```python
def test_agent_notified_orchestrator_carries_text():
    bus = EventBus()
    received: list[AgentNotifiedOrchestrator] = []
    bus.subscribe(AgentNotifiedOrchestrator, received.append)

    bus.publish(AgentNotifiedOrchestrator(agent_id="a1", message="task complete"))
    assert received[0].agent_id == "a1"
    assert received[0].message == "task complete"


def test_direct_message_to_agent_carries_text():
    bus = EventBus()
    received: list[DirectMessageToAgent] = []
    bus.subscribe(DirectMessageToAgent, received.append)

    bus.publish(DirectMessageToAgent(agent_id="a1", text="hi from user"))
    assert received[0].agent_id == "a1"
    assert received[0].text == "hi from user"
```

Update the import line at the top to include the new names:

```python
from patchfeld.events import (
    AgentMessageAppended,
    AgentNotifiedOrchestrator,
    AgentSpawned,
    AgentStateChanged,
    DirectMessageToAgent,
    EventBus,
)
```

- [ ] **Step 2: Run the failing tests**

```bash
.venv/bin/pytest tests/test_agent_events.py -v
```

Expected: ImportError on the two new names.

- [ ] **Step 3: Append to `patchfeld/events.py`**

```python
@dataclass(frozen=True)
class AgentNotifiedOrchestrator:
    """A child agent called notify_orchestrator (fire-and-forget)."""
    agent_id: str
    message: str


@dataclass(frozen=True)
class DirectMessageToAgent:
    """User typed directly to a focused AgentTranscript's input."""
    agent_id: str
    text: str
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest tests/test_agent_events.py -v
.venv/bin/pytest -q
```

Expected: 5 tests pass (3 plan-2 + 2 new); full suite green.

- [ ] **Step 5: Commit**

```bash
git add patchfeld/events.py tests/test_agent_events.py
git commit -m "feat(events): AgentNotifiedOrchestrator + DirectMessageToAgent"
```

---

## Task 4 — Serialize `AgentSession.send` behind an `asyncio.Lock`

**Files:**
- Modify: `patchfeld/agents/session.py`
- Test: `tests/test_agent_session_send_lock.py`

The plan-2 final reviewer flagged: two concurrent `send()` calls race — the second's `_consume_stream` task overwrites `self._stream_task`, so `wait_idle` may return early. Wrap `send` in an `asyncio.Lock` and gate the next send on the prior `_consume_stream` task completing.

- [ ] **Step 1: Write the failing test**

Create `tests/test_agent_session_send_lock.py`:

```python
import asyncio
from pathlib import Path

import pytest
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
)

from patchfeld.agents.fake_sdk_adapter import FakeSDKAdapter
from patchfeld.agents.session import AgentSession
from patchfeld.agents.state import AgentInfo
from patchfeld.events import AgentMessageAppended, EventBus
from patchfeld.persistence.transcript_store import AgentTranscript


def _info() -> AgentInfo:
    return AgentInfo(id="a1", name="lock-test", cwd="/tmp", started_at=100.0)


def _script(text: str) -> list:
    return [
        AssistantMessage(content=[TextBlock(text=text)], model="fake-model"),
        ResultMessage(
            subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
            num_turns=1, session_id="fake", total_cost_usd=0.0,
            usage={"input_tokens": 1, "output_tokens": 1}, result=text,
        ),
    ]


@pytest.mark.asyncio
async def test_concurrent_sends_are_serialized_and_both_complete(tmp_path: Path):
    bus = EventBus()
    appended: list[AgentMessageAppended] = []
    bus.subscribe(AgentMessageAppended, appended.append)

    adapter = FakeSDKAdapter(scripts=[_script("first reply"), _script("second reply")])
    session = AgentSession(
        info=_info(),
        adapter=adapter,
        transcript=AgentTranscript(cwd=tmp_path, agent_id="a1"),
        bus=bus,
    )
    await session.start(options=ClaudeAgentOptions())

    # Fire two sends back-to-back without waiting for the first to drain.
    t1 = asyncio.create_task(session.send("first"))
    t2 = asyncio.create_task(session.send("second"))
    await asyncio.gather(t1, t2)
    await session.wait_idle()

    user_texts = [a.text for a in appended if a.role == "user"]
    assistant_texts = [a.text for a in appended if a.role == "assistant"]

    # Both user prompts and both assistant replies must appear, in order.
    assert user_texts == ["first", "second"]
    assert assistant_texts == ["first reply", "second reply"]
```

- [ ] **Step 2: Run the test and confirm it fails (the second reply gets lost or order is wrong)**

```bash
.venv/bin/pytest tests/test_agent_session_send_lock.py -v
```

Expected: FAILURE (the assertion may show only the second user/assistant pair, or out-of-order).

- [ ] **Step 3: Modify `patchfeld/agents/session.py`**

Add an `asyncio.Lock` and use it inside `send`. Replace the `__init__` and `send` methods only — leave the rest of the class unchanged.

In `__init__`, add (alongside the existing `self._idle_event` line):

```python
        self._send_lock = asyncio.Lock()
```

Replace the `send` method body with:

```python
    async def send(self, prompt: str) -> None:
        async with self._send_lock:
            # If the previous stream is still draining, wait for it before
            # issuing the next query — the SDK doesn't support overlapping
            # query() calls on a single session.
            if self._stream_task is not None and not self._stream_task.done():
                await self._stream_task

            self._record(role="user", text=prompt)
            await self._adapter.query(prompt)
            self._set_state(AgentState.RUNNING)
            self._idle_event.clear()
            self._stream_task = asyncio.create_task(self._consume_stream())
```

- [ ] **Step 4: Run the test**

```bash
.venv/bin/pytest tests/test_agent_session_send_lock.py -v
.venv/bin/pytest -q
```

Expected: new test passes; full suite still green.

- [ ] **Step 5: Commit**

```bash
git add patchfeld/agents/session.py tests/test_agent_session_send_lock.py
git commit -m "fix(agents): serialize AgentSession.send via Lock; await prior stream"
```

---

## Task 5 — Serialize the orchestrator's `_on_user_message` race

**Files:**
- Modify: `patchfeld/orchestrator/session.py`
- Test: `tests/test_orchestrator_session_serializes.py`

Now that `AgentSession.send` is safe under concurrent calls (Task 4), the orchestrator's `asyncio.create_task(self._inner.send(...))` is technically OK — the inner lock will serialize. But a second user message arriving while the first is still mid-stream will be silently queued behind the lock. We add a small test that pins the documented behavior.

- [ ] **Step 1: Write the test**

Create `tests/test_orchestrator_session_serializes.py`:

```python
import asyncio

import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

from patchfeld.agents.fake_sdk_adapter import FakeSDKAdapter
from patchfeld.agents.manager import AgentManager
from patchfeld.events import EventBus, OrchestratorReply, UserMessageToOrchestrator
from patchfeld.orchestrator.session import OrchestratorSession


def _script(text: str) -> list:
    return [
        AssistantMessage(content=[TextBlock(text=text)], model="fake-model"),
        ResultMessage(
            subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
            num_turns=1, session_id="fake", total_cost_usd=0.0,
            usage={"input_tokens": 1, "output_tokens": 1}, result=text,
        ),
    ]


@pytest.mark.asyncio
async def test_two_user_messages_in_quick_succession_both_get_replies(tmp_path):
    bus = EventBus()
    replies: list[OrchestratorReply] = []
    bus.subscribe(OrchestratorReply, replies.append)

    manager = AgentManager(
        cwd=tmp_path,
        bus=bus,
        adapter_factory=lambda: FakeSDKAdapter(scripts=[_script("ok")]),
    )
    session = OrchestratorSession(
        cwd=tmp_path,
        bus=bus,
        manager=manager,
        adapter=FakeSDKAdapter(scripts=[_script("first reply"), _script("second reply")]),
    )
    await session.start()

    bus.publish(UserMessageToOrchestrator("first"))
    bus.publish(UserMessageToOrchestrator("second"))
    await session.wait_idle()

    reply_texts = [r.text for r in replies]
    assert reply_texts == ["first reply", "second reply"]
```

- [ ] **Step 2: Run and confirm it fails OR passes**

```bash
.venv/bin/pytest tests/test_orchestrator_session_serializes.py -v
```

If the test passes already (because Task 4's lock made things safe), great — proceed to Step 4 to make the wait_idle behavior more robust. If it fails, the issue is in `wait_idle`'s single yield not being enough to observe both create_tasks. Continue with Step 3.

- [ ] **Step 3: Make `wait_idle` more robust against pending create_tasks**

In `patchfeld/orchestrator/session.py`, replace `wait_idle` with:

```python
    async def wait_idle(self) -> None:
        # Drain any UserMessageToOrchestrator-triggered create_tasks that may
        # have been scheduled but not yet started. Two yields is enough to
        # cover the worst case (sync subscribe → create_task → coroutine
        # body's first await).
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        await self._inner.wait_idle()
```

- [ ] **Step 4: Run the test and the full suite**

```bash
.venv/bin/pytest tests/test_orchestrator_session_serializes.py -v
.venv/bin/pytest -q
```

Expected: passes; full suite green.

- [ ] **Step 5: Commit**

```bash
git add patchfeld/orchestrator/session.py tests/test_orchestrator_session_serializes.py
git commit -m "fix(orchestrator): wait_idle drains pending create_tasks; pin contract"
```

---

## Task 6 — `AgentManager.send` and `get_inbox`

**Files:**
- Modify: `patchfeld/agents/manager.py`
- Test: `tests/test_agent_manager.py` (append)

The orchestrator's `send_to_agent` MCP tool delegates to `AgentManager.send(agent_id, prompt)`. The child tools (Task 7) will need `manager.get_inbox(agent_id)` to register/resolve futures.

- [ ] **Step 1: Append tests to `tests/test_agent_manager.py`**

```python
@pytest.mark.asyncio
async def test_send_routes_followup_to_existing_agent(tmp_path: Path):
    manager = AgentManager(
        cwd=tmp_path,
        bus=EventBus(),
        adapter_factory=lambda: FakeSDKAdapter(scripts=[_ok_script(), _ok_script()]),
    )
    aid = await manager.spawn(name="research", prompt="first prompt")
    await manager.wait_idle(aid)

    await manager.send(aid, "follow up")
    await manager.wait_idle(aid)

    entries = manager.read_transcript(aid)
    user_texts = [e.text for e in entries if e.role == "user"]
    assert user_texts == ["first prompt", "follow up"]


@pytest.mark.asyncio
async def test_send_to_unknown_agent_raises_keyerror(tmp_path: Path):
    manager = AgentManager(
        cwd=tmp_path,
        bus=EventBus(),
        adapter_factory=lambda: FakeSDKAdapter(scripts=[_ok_script()]),
    )
    with pytest.raises(KeyError):
        await manager.send("does-not-exist", "hi")


@pytest.mark.asyncio
async def test_get_inbox_returns_a_request_inbox_per_agent(tmp_path: Path):
    from patchfeld.agents.request_inbox import RequestInbox

    manager = AgentManager(
        cwd=tmp_path,
        bus=EventBus(),
        adapter_factory=lambda: FakeSDKAdapter(scripts=[_ok_script()]),
    )
    aid = await manager.spawn(name="research", prompt="hi")
    await manager.wait_idle(aid)

    inbox = manager.get_inbox(aid)
    assert isinstance(inbox, RequestInbox)

    # Same agent → same inbox instance (so registrations and resolutions match up).
    assert manager.get_inbox(aid) is inbox

    # Unknown agent → None (don't raise).
    assert manager.get_inbox("nope") is None
```

- [ ] **Step 2: Run the new tests and confirm they fail**

```bash
.venv/bin/pytest tests/test_agent_manager.py -v
```

Expected: 3 new failures (`AgentManager.send` and `get_inbox` don't exist yet).

- [ ] **Step 3: Modify `patchfeld/agents/manager.py`**

Add the import at the top:

```python
from patchfeld.agents.request_inbox import RequestInbox
```

Initialize the per-agent inbox map in `__init__` (alongside `self._sessions`):

```python
        self._inboxes: dict[str, RequestInbox] = {}
```

In `spawn`, after creating the session and BEFORE the first `await session.start(...)`, add:

```python
        self._inboxes[agent_id] = RequestInbox()
```

In `kill`, also pop the inbox:

```python
    async def kill(self, agent_id: str) -> None:
        session = self._sessions.pop(agent_id, None)
        self._inboxes.pop(agent_id, None)
        if session is not None:
            await session.stop()
```

Add two new methods to the class body (anywhere after `read_transcript`):

```python
    async def send(self, agent_id: str, text: str) -> None:
        session = self._sessions.get(agent_id)
        if session is None:
            raise KeyError(f"unknown agent_id: {agent_id}")
        await session.send(text)

    def get_inbox(self, agent_id: str) -> RequestInbox | None:
        return self._inboxes.get(agent_id)
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest tests/test_agent_manager.py -v
.venv/bin/pytest -q
```

Expected: all manager tests pass (5 plan-2 + 3 new); full suite green.

- [ ] **Step 5: Commit**

```bash
git add patchfeld/agents/manager.py tests/test_agent_manager.py
git commit -m "feat(agents): AgentManager.send + get_inbox; per-agent RequestInbox"
```

---

## Task 7 — Child-side MCP tools (`notify_orchestrator`, `ask_orchestrator`)

**Files:**
- Create: `patchfeld/agents/child_tools.py`
- Test: `tests/test_child_tools.py`

Each child agent gets these two tools injected via its `ClaudeAgentOptions.mcp_servers`. They publish events / await futures, both via the bus.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_child_tools.py`:

```python
import asyncio

import pytest

from patchfeld.agents.child_tools import build_child_tools
from patchfeld.agents.request_inbox import RequestInbox
from patchfeld.events import (
    AgentNotifiedOrchestrator,
    EventBus,
)


@pytest.mark.asyncio
async def test_notify_orchestrator_publishes_event_and_returns():
    bus = EventBus()
    received: list[AgentNotifiedOrchestrator] = []
    bus.subscribe(AgentNotifiedOrchestrator, received.append)

    inbox = RequestInbox()
    notify, _ask = build_child_tools(agent_id="a1", bus=bus, inbox=inbox)

    out = await notify({"message": "tests passed"})

    assert received == [AgentNotifiedOrchestrator(agent_id="a1", message="tests passed")]
    assert "delivered" in out["content"][0]["text"].lower()


@pytest.mark.asyncio
async def test_ask_orchestrator_blocks_until_inbox_resolves():
    bus = EventBus()
    inbox = RequestInbox()
    _notify, ask = build_child_tools(agent_id="a1", bus=bus, inbox=inbox)

    async def resolver():
        await asyncio.sleep(0)
        # The first pending request id is the one ask just registered.
        pending = inbox.pending()
        assert len(pending) == 1
        inbox.resolve(pending[0], "ship it")

    asyncio.create_task(resolver())
    out = await ask({"question": "go/no-go?"})
    assert out["content"][0]["text"] == "ship it"


@pytest.mark.asyncio
async def test_ask_orchestrator_times_out():
    bus = EventBus()
    inbox = RequestInbox()
    _notify, ask = build_child_tools(agent_id="a1", bus=bus, inbox=inbox)

    out = await ask({"question": "anyone there?", "timeout_s": 0.05})
    text = out["content"][0]["text"].lower()
    assert "timeout" in text or "timed out" in text
```

- [ ] **Step 2: Run and confirm they fail**

```bash
.venv/bin/pytest tests/test_child_tools.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement `patchfeld/agents/child_tools.py`**

```python
import asyncio

from claude_agent_sdk import create_sdk_mcp_server, tool

from patchfeld.agents.request_inbox import RequestInbox
from patchfeld.events import (
    AgentNotifiedOrchestrator,
    AgentRequestedUserInput,
    EventBus,
)


def build_child_tools(*, agent_id: str, bus: EventBus, inbox: RequestInbox):
    """Return (notify_handler, ask_handler) — bare async callables for unit tests."""

    async def notify_orchestrator(args: dict) -> dict:
        message = args["message"]
        bus.publish(AgentNotifiedOrchestrator(agent_id=agent_id, message=message))
        return {"content": [{"type": "text", "text": "Notification delivered."}]}

    async def ask_orchestrator(args: dict) -> dict:
        question = args["question"]
        timeout_s = float(args.get("timeout_s", 300))
        request_id = inbox.register()
        bus.publish(
            AgentRequestedUserInput(
                agent_id=agent_id, question=question, request_id=request_id
            )
        )
        try:
            response = await inbox.wait(request_id, timeout_s=timeout_s)
            return {"content": [{"type": "text", "text": response}]}
        except asyncio.TimeoutError:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"ask_orchestrator timed out after {timeout_s}s "
                            "with no response."
                        ),
                    }
                ]
            }

    return notify_orchestrator, ask_orchestrator


def build_child_mcp_server(*, agent_id: str, bus: EventBus, inbox: RequestInbox):
    notify_h, ask_h = build_child_tools(agent_id=agent_id, bus=bus, inbox=inbox)
    notify = tool(
        "notify_orchestrator",
        "Send a fire-and-forget notification to the orchestrator.",
        {"message": str},
    )(notify_h)
    ask = tool(
        "ask_orchestrator",
        (
            "Ask the orchestrator a question and block until they respond. "
            "Optional timeout_s defaults to 300 seconds."
        ),
        {"question": str},  # timeout_s is optional, not in schema
    )(ask_h)
    return create_sdk_mcp_server(
        name="patchfeld_child", version="1.0.0", tools=[notify, ask]
    )
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest tests/test_child_tools.py -v
.venv/bin/pytest -q
```

Expected: 3 new pass; full suite green.

- [ ] **Step 5: Commit**

```bash
git add patchfeld/agents/child_tools.py tests/test_child_tools.py
git commit -m "feat(agents): child-side MCP tools — notify_orchestrator + ask_orchestrator"
```

---

## Task 8 — Inject child tools into every spawned agent

**Files:**
- Modify: `patchfeld/agents/manager.py`

Now that we have a child MCP server builder, hand it to every spawned `ClaudeAgentOptions`.

- [ ] **Step 1: Modify `AgentManager.spawn`**

Add the import:

```python
from patchfeld.agents.child_tools import build_child_mcp_server
```

In `spawn`, after constructing `info` and `transcript` but before `await session.start(...)`, build the child MCP server. Replace the `options_kwargs` construction block with:

```python
        child_mcp = build_child_mcp_server(
            agent_id=agent_id, bus=self._bus, inbox=self._inboxes[agent_id]
        )
        options_kwargs: dict = {
            "cwd": info.cwd,
            "permission_mode": "bypassPermissions",
            "mcp_servers": {"patchfeld_child": child_mcp},
        }
        if allowed_tools is not None:
            options_kwargs["allowed_tools"] = allowed_tools
```

(Everything below `if allowed_tools is not None:` stays as before.)

- [ ] **Step 2: Verify nothing broke**

```bash
.venv/bin/pytest -q
```

Expected: full suite green. The existing manager tests use `FakeSDKAdapter` which doesn't actually invoke MCP tools — so wiring the server in `options_kwargs` is a no-op for tests, but real children will now have the tools.

- [ ] **Step 3: Commit**

```bash
git add patchfeld/agents/manager.py
git commit -m "feat(agents): inject notify_orchestrator + ask_orchestrator into every child"
```

---

## Task 9 — Orchestrator MCP tools: `send_to_agent`, `interrupt_agent`, `kill_agent`, `respond_to_agent_request`

**Files:**
- Modify: `patchfeld/orchestrator/tools.py`
- Test: `tests/test_orchestrator_tools_send_interrupt_kill.py`
- Test: `tests/test_orchestrator_tools_respond.py`

Four new entries in `_SPECS`. Each has a builder function that closes over the manager.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_orchestrator_tools_send_interrupt_kill.py`:

```python
import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

from patchfeld.agents.fake_sdk_adapter import FakeSDKAdapter
from patchfeld.agents.manager import AgentManager
from patchfeld.events import EventBus
from patchfeld.orchestrator.tools import build_orchestrator_tools


def _script(text: str) -> list:
    return [
        AssistantMessage(content=[TextBlock(text=text)], model="fake-model"),
        ResultMessage(
            subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
            num_turns=1, session_id="fake", total_cost_usd=0.0,
            usage={"input_tokens": 1, "output_tokens": 1}, result=text,
        ),
    ]


def _make_manager(tmp_path):
    return AgentManager(
        cwd=tmp_path,
        bus=EventBus(),
        adapter_factory=lambda: FakeSDKAdapter(scripts=[_script("first"), _script("second")]),
    )


@pytest.mark.asyncio
async def test_send_to_agent_appends_followup_to_transcript(tmp_path):
    manager = _make_manager(tmp_path)
    spawn, _list, _read, send, _interrupt, _kill, _respond = build_orchestrator_tools(manager)

    await spawn({"name": "alpha", "prompt": "say first"})
    aid = manager.list_infos()[0].id
    await manager.wait_idle(aid)

    await send({"agent_id": aid, "message": "say second"})
    await manager.wait_idle(aid)

    entries = manager.read_transcript(aid)
    user_texts = [e.text for e in entries if e.role == "user"]
    assert user_texts == ["say first", "say second"]


@pytest.mark.asyncio
async def test_kill_agent_removes_session(tmp_path):
    manager = _make_manager(tmp_path)
    spawn, _list, _read, _send, _interrupt, kill, _respond = build_orchestrator_tools(manager)

    await spawn({"name": "alpha", "prompt": "say first"})
    aid = manager.list_infos()[0].id
    await manager.wait_idle(aid)

    out = await kill({"agent_id": aid})
    assert "killed" in out["content"][0]["text"].lower()
    assert manager.get_session(aid) is None


@pytest.mark.asyncio
async def test_interrupt_agent_calls_interrupt(tmp_path):
    manager = _make_manager(tmp_path)
    spawn, _list, _read, _send, interrupt, _kill, _respond = build_orchestrator_tools(manager)

    await spawn({"name": "alpha", "prompt": "say first"})
    aid = manager.list_infos()[0].id

    out = await interrupt({"agent_id": aid})
    # The fake adapter's interrupt is a no-op, but the tool should at least
    # find the agent and not raise.
    assert "interrupt" in out["content"][0]["text"].lower()
```

Create `tests/test_orchestrator_tools_respond.py`:

```python
import asyncio

import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

from patchfeld.agents.fake_sdk_adapter import FakeSDKAdapter
from patchfeld.agents.manager import AgentManager
from patchfeld.events import EventBus
from patchfeld.orchestrator.tools import build_orchestrator_tools


def _ok():
    return [
        AssistantMessage(content=[TextBlock(text="done")], model="fake-model"),
        ResultMessage(
            subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
            num_turns=1, session_id="fake", total_cost_usd=0.0,
            usage={"input_tokens": 1, "output_tokens": 1}, result="done",
        ),
    ]


@pytest.mark.asyncio
async def test_respond_to_agent_request_resolves_pending_inbox_entry(tmp_path):
    manager = AgentManager(
        cwd=tmp_path,
        bus=EventBus(),
        adapter_factory=lambda: FakeSDKAdapter(scripts=[_ok()]),
    )
    spawn, _list, _read, _send, _interrupt, _kill, respond = build_orchestrator_tools(manager)

    await spawn({"name": "alpha", "prompt": "hi"})
    aid = manager.list_infos()[0].id
    await manager.wait_idle(aid)

    inbox = manager.get_inbox(aid)
    request_id = inbox.register()

    waiter = asyncio.create_task(inbox.wait(request_id, timeout_s=1.0))
    out = await respond(
        {"agent_id": aid, "request_id": request_id, "response": "ship it"}
    )
    assert "resolved" in out["content"][0]["text"].lower()

    answer = await waiter
    assert answer == "ship it"


@pytest.mark.asyncio
async def test_respond_to_unknown_agent_returns_error_text(tmp_path):
    manager = AgentManager(
        cwd=tmp_path,
        bus=EventBus(),
        adapter_factory=lambda: FakeSDKAdapter(scripts=[_ok()]),
    )
    _spawn, _list, _read, _send, _interrupt, _kill, respond = build_orchestrator_tools(manager)

    out = await respond(
        {"agent_id": "nope", "request_id": "x", "response": "anything"}
    )
    assert "unknown" in out["content"][0]["text"].lower() or "no inbox" in out["content"][0]["text"].lower()
```

- [ ] **Step 2: Run failing tests**

```bash
.venv/bin/pytest tests/test_orchestrator_tools_send_interrupt_kill.py tests/test_orchestrator_tools_respond.py -v
```

Expected: failures (the build_orchestrator_tools tuple has only 3 entries).

- [ ] **Step 3: Add four new tool builders to `patchfeld/orchestrator/tools.py`**

Append these four builder functions ABOVE `_SPECS`:

```python
def _send_handler(manager: AgentManager):
    async def send_to_agent(args: dict) -> dict:
        agent_id = args["agent_id"]
        message = args["message"]
        try:
            await manager.send(agent_id, message)
            return {
                "content": [
                    {"type": "text", "text": f"Sent to {agent_id}: {message[:60]}"}
                ]
            }
        except KeyError:
            return {"content": [{"type": "text", "text": f"Unknown agent_id: {agent_id}"}]}
    return send_to_agent


def _interrupt_handler(manager: AgentManager):
    async def interrupt_agent(args: dict) -> dict:
        agent_id = args["agent_id"]
        if manager.get_session(agent_id) is None:
            return {"content": [{"type": "text", "text": f"Unknown agent_id: {agent_id}"}]}
        await manager.interrupt(agent_id)
        return {"content": [{"type": "text", "text": f"Sent interrupt to {agent_id}."}]}
    return interrupt_agent


def _kill_handler(manager: AgentManager):
    async def kill_agent(args: dict) -> dict:
        agent_id = args["agent_id"]
        if manager.get_session(agent_id) is None:
            return {"content": [{"type": "text", "text": f"Unknown agent_id: {agent_id}"}]}
        await manager.kill(agent_id)
        return {"content": [{"type": "text", "text": f"Killed agent {agent_id}."}]}
    return kill_agent


def _respond_handler(manager: AgentManager):
    async def respond_to_agent_request(args: dict) -> dict:
        agent_id = args["agent_id"]
        request_id = args["request_id"]
        response = args["response"]
        inbox = manager.get_inbox(agent_id)
        if inbox is None:
            return {
                "content": [{"type": "text", "text": f"Unknown agent_id (no inbox): {agent_id}"}]
            }
        inbox.resolve(request_id, response)
        return {
            "content": [
                {"type": "text", "text": f"Resolved request {request_id} for {agent_id}."}
            ]
        }
    return respond_to_agent_request
```

Append these four `_ToolSpec` entries to the `_SPECS` list (in addition to the existing three):

```python
    _ToolSpec(
        name="send_to_agent",
        description=(
            "Send a follow-up message to an existing agent. The agent will "
            "process it as a new turn."
        ),
        input_schema={"agent_id": str, "message": str},
        build=_send_handler,
    ),
    _ToolSpec(
        name="interrupt_agent",
        description="Interrupt the agent's current generation, if any.",
        input_schema={"agent_id": str},
        build=_interrupt_handler,
    ),
    _ToolSpec(
        name="kill_agent",
        description="Stop and remove an agent session.",
        input_schema={"agent_id": str},
        build=_kill_handler,
    ),
    _ToolSpec(
        name="respond_to_agent_request",
        description=(
            "Respond to an agent's pending ask_orchestrator request, "
            "identified by request_id."
        ),
        input_schema={"agent_id": str, "request_id": str, "response": str},
        build=_respond_handler,
    ),
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest tests/test_orchestrator_tools_send_interrupt_kill.py tests/test_orchestrator_tools_respond.py -v
.venv/bin/pytest -q
```

Expected: 5 new pass; full suite green. The existing `test_orchestrator_tools.py` tests should also pass (they unpack only the first 3 tuple elements).

- [ ] **Step 5: Commit**

```bash
git add patchfeld/orchestrator/tools.py tests/test_orchestrator_tools_send_interrupt_kill.py tests/test_orchestrator_tools_respond.py
git commit -m "feat(orchestrator): send_to_agent / interrupt_agent / kill_agent / respond_to_agent_request"
```

---

## Task 10 — OrchestratorSession reacts to child events (notify + ask)

**Files:**
- Modify: `patchfeld/orchestrator/session.py`

When a child publishes `AgentNotifiedOrchestrator` or `AgentRequestedUserInput`, we want the orchestrator to know about it. Two options for surfacing it:

(a) **Synthesize as a user message into the orchestrator's session** so the orchestrator AI receives it in-flow and can decide what to do. Direct, but means the orchestrator is "spoken to" by children.

(b) **Surface as an `OrchestratorReply` line** so the human sees it. Passive — the orchestrator AI doesn't react until the human prompts it.

Plan-3 ships **(a)** for `AgentRequestedUserInput` (since it's blocking — we want the orchestrator to respond) and **(a)** for `AgentNotifiedOrchestrator` (since the orchestrator may want to follow up). Both arrive as synthetic `UserMessageToOrchestrator` events on the bus, formatted with the agent name and (for ask) the request_id so the orchestrator AI knows which `respond_to_agent_request` call to make.

- [ ] **Step 1: Modify `patchfeld/orchestrator/session.py`**

Add the imports:

```python
from patchfeld.events import (
    AgentMessageAppended,
    AgentNotifiedOrchestrator,
    AgentRequestedUserInput,
    EventBus,
    OrchestratorReply,
    UserMessageToOrchestrator,
)
```

(Add `AgentNotifiedOrchestrator` and `AgentRequestedUserInput` to the existing import block.)

In `__init__`, alongside the existing `_unsub_*` lambdas, add:

```python
        self._unsub_notify: callable = lambda: None
        self._unsub_ask: callable = lambda: None
```

In `start`, alongside the existing two subscriptions, add:

```python
        self._unsub_notify = self._bus.subscribe(
            AgentNotifiedOrchestrator, self._on_child_notified
        )
        self._unsub_ask = self._bus.subscribe(
            AgentRequestedUserInput, self._on_child_asked
        )
```

In `stop`, alongside the existing two unsubscribes, add:

```python
        self._unsub_notify()
        self._unsub_ask()
```

Append two new internal handler methods:

```python
    def _on_child_notified(self, event: AgentNotifiedOrchestrator) -> None:
        synthetic = (
            f"[from agent {event.agent_id}] {event.message}"
        )
        self._bus.publish(UserMessageToOrchestrator(synthetic))

    def _on_child_asked(self, event: AgentRequestedUserInput) -> None:
        synthetic = (
            f"[agent {event.agent_id} is blocked waiting for your reply, "
            f"request_id={event.request_id}] question: {event.question}\n"
            f"Use respond_to_agent_request(agent_id={event.agent_id!r}, "
            f"request_id={event.request_id!r}, response=...) to unblock."
        )
        self._bus.publish(UserMessageToOrchestrator(synthetic))
```

- [ ] **Step 2: Sanity check no existing tests broke**

```bash
.venv/bin/pytest -q
```

Expected: full suite green. (The new handlers are dormant unless something fires those events; existing tests don't.)

- [ ] **Step 3: Commit**

```bash
git add patchfeld/orchestrator/session.py
git commit -m "feat(orchestrator): inject child notify/ask events into orchestrator stream"
```

---

## Task 11 — `AgentTranscript` widget grows an input box

**Files:**
- Modify: `patchfeld/widgets/agent_transcript.py`
- Test: `tests/test_agent_transcript_input.py`

The widget gets a bottom Input. Submitting publishes `DirectMessageToAgent(agent_id, text)`. The user types directly into a child agent's transcript when it's the focused panel.

- [ ] **Step 1: Write the failing test**

Create `tests/test_agent_transcript_input.py`:

```python
import pytest
from textual.app import App
from textual.widgets import Input

from patchfeld.events import DirectMessageToAgent, EventBus
from patchfeld.widgets.agent_transcript import AgentTranscript


class _HostApp(App):
    def __init__(self, bus: EventBus, agent_id: str) -> None:
        super().__init__()
        self.event_bus = bus
        self._agent_id = agent_id

    def compose(self):
        yield AgentTranscript(agent_id=self._agent_id, event_bus=self.event_bus)


@pytest.mark.asyncio
async def test_typing_into_input_publishes_direct_message_event(tmp_path):
    bus = EventBus()
    received: list[DirectMessageToAgent] = []
    bus.subscribe(DirectMessageToAgent, received.append)

    app = _HostApp(bus, "a1")
    app.cwd = tmp_path
    async with app.run_test() as pilot:
        await pilot.pause()
        # Find the Input child of AgentTranscript and submit a value.
        widget = app.query_one(AgentTranscript)
        input_box = widget.query_one(Input)
        input_box.value = "hi from user"
        # Simulate enter.
        from textual.widgets._input import Input as InputCls
        await input_box.action_submit()
        await pilot.pause()

    assert received == [DirectMessageToAgent(agent_id="a1", text="hi from user")]
```

(If `action_submit` isn't a public method on `Input` in the installed Textual version, fall back to dispatching the `Input.Submitted` message directly via `widget.post_message(InputCls.Submitted(input=input_box, value="hi from user"))`. Pick whichever the SDK actually exposes — STOP and report if neither works.)

- [ ] **Step 2: Run the failing test**

```bash
.venv/bin/pytest tests/test_agent_transcript_input.py -v
```

Expected: the input box doesn't exist on the widget yet; query_one(Input) raises NoMatches.

- [ ] **Step 3: Modify `patchfeld/widgets/agent_transcript.py`**

Replace the file's contents with:

```python
from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Input, Static

from patchfeld.events import (
    AgentMessageAppended,
    DirectMessageToAgent,
    EventBus,
)
from patchfeld.persistence.transcript_store import AgentTranscript as TranscriptStore


class AgentTranscript(Vertical):
    """Scrollable, live-updating transcript view for one agent, with input."""

    DEFAULT_CSS = """
    AgentTranscript {
        border: round $surface-lighten-2;
        padding: 0 1;
    }
    AgentTranscript #transcript-scroll {
        height: 1fr;
    }
    AgentTranscript #transcript-input {
        dock: bottom;
        height: 3;
    }
    AgentTranscript .role-user { color: $accent; }
    AgentTranscript .role-assistant { color: $text; }
    AgentTranscript .role-tool_use { color: $warning; }
    AgentTranscript .role-tool_result { color: $secondary; }
    AgentTranscript .role-thinking { color: $text-muted; }
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
        self._unsub = lambda: None
        self._lines: list[str] = []

    def compose(self) -> ComposeResult:
        yield VerticalScroll(id="transcript-scroll")
        yield Input(placeholder=f"Message {self._agent_id}…", id="transcript-input")

    def on_mount(self) -> None:
        bus = self._bus or getattr(self.app, "event_bus", None)
        cwd: Path | None = getattr(self.app, "cwd", None)
        if cwd is not None:
            store = TranscriptStore(cwd=cwd, agent_id=self._agent_id)
            for entry in store.read_all():
                self._append_line(entry.role, entry.text)
        if bus is not None:
            self._unsub = bus.subscribe(AgentMessageAppended, self._on_appended)

    def on_unmount(self) -> None:
        self._unsub()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return
        bus = self._bus or getattr(self.app, "event_bus", None)
        if bus is not None:
            bus.publish(DirectMessageToAgent(agent_id=self._agent_id, text=text))
        event.input.value = ""

    def _on_appended(self, event: AgentMessageAppended) -> None:
        if event.agent_id != self._agent_id:
            return
        self._append_line(event.role, event.text)

    def _append_line(self, role: str, text: str) -> None:
        scroll = self.query_one("#transcript-scroll", VerticalScroll)
        widget = Static(
            f"[role-{role}]{role}:[/role-{role}] {text}", classes=f"role-{role}"
        )
        self._lines.append(f"[{role}] {text}")
        scroll.mount(widget)
        scroll.scroll_end(animate=False)

    def rendered_text(self) -> str:
        """Test helper — returns concatenated rendered text."""
        return "\n".join(self._lines)
```

Key changes vs plan 2:
- Now subclasses `Vertical` instead of `VerticalScroll`
- `compose` yields a `VerticalScroll` (named `#transcript-scroll`) and an `Input` (named `#transcript-input`)
- Lines are mounted into the inner scroll, not into self
- New `on_input_submitted` publishes `DirectMessageToAgent`
- `_append_line` was updated to mount into `#transcript-scroll`

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest tests/test_agent_transcript_input.py tests/test_agent_transcript_widget.py -v
.venv/bin/pytest -q
```

Expected: new test passes; existing widget tests still pass; full suite green.

- [ ] **Step 5: Commit**

```bash
git add patchfeld/widgets/agent_transcript.py tests/test_agent_transcript_input.py
git commit -m "feat(widgets): AgentTranscript grows an input → DirectMessageToAgent"
```

---

## Task 12 — Wire `DirectMessageToAgent` to AgentManager.send

**Files:**
- Modify: `patchfeld/agents/manager.py`
- Test: `tests/test_direct_message_to_agent.py`

The `AgentManager` subscribes to `DirectMessageToAgent` on the bus and routes each event to `self.send(agent_id, text)`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_direct_message_to_agent.py`:

```python
import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

from patchfeld.agents.fake_sdk_adapter import FakeSDKAdapter
from patchfeld.agents.manager import AgentManager
from patchfeld.events import DirectMessageToAgent, EventBus


def _ok():
    return [
        AssistantMessage(content=[TextBlock(text="ack")], model="fake-model"),
        ResultMessage(
            subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
            num_turns=1, session_id="fake", total_cost_usd=0.0,
            usage={"input_tokens": 1, "output_tokens": 1}, result="ack",
        ),
    ]


@pytest.mark.asyncio
async def test_direct_message_event_routes_to_session_send(tmp_path):
    bus = EventBus()
    manager = AgentManager(
        cwd=tmp_path,
        bus=bus,
        adapter_factory=lambda: FakeSDKAdapter(scripts=[_ok(), _ok()]),
    )
    aid = await manager.spawn(name="alpha", prompt="initial")
    await manager.wait_idle(aid)

    bus.publish(DirectMessageToAgent(agent_id=aid, text="from user"))
    await manager.wait_idle(aid)

    entries = manager.read_transcript(aid)
    user_texts = [e.text for e in entries if e.role == "user"]
    assert user_texts == ["initial", "from user"]


@pytest.mark.asyncio
async def test_direct_message_to_unknown_agent_is_silently_ignored(tmp_path):
    bus = EventBus()
    manager = AgentManager(
        cwd=tmp_path,
        bus=bus,
        adapter_factory=lambda: FakeSDKAdapter(scripts=[_ok()]),
    )
    # No spawn — agent_id doesn't exist.
    bus.publish(DirectMessageToAgent(agent_id="ghost", text="hi"))
    # Nothing should raise; nothing else to assert.
```

- [ ] **Step 2: Run the failing tests**

```bash
.venv/bin/pytest tests/test_direct_message_to_agent.py -v
```

Expected: the first test fails because the manager doesn't subscribe; the second silently passes (nothing raises in current impl, just nothing happens).

- [ ] **Step 3: Modify `patchfeld/agents/manager.py`**

Add the import:

```python
from patchfeld.events import (
    AgentSpawned,
    AgentStateChanged,
    DirectMessageToAgent,
    EventBus,
)
```

(Add `DirectMessageToAgent` to the existing import block.)

In `__init__`, alongside `self._unsub_state`, add:

```python
        self._unsub_direct = bus.subscribe(DirectMessageToAgent, self._on_direct_message)
```

In `shutdown`, alongside `self._unsub_state()`, add:

```python
        self._unsub_direct()
```

Append a new internal handler:

```python
    def _on_direct_message(self, event: DirectMessageToAgent) -> None:
        session = self._sessions.get(event.agent_id)
        if session is None:
            return  # silently ignore stale messages for dead agents
        import asyncio as _asyncio
        _asyncio.create_task(session.send(event.text))
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest tests/test_direct_message_to_agent.py -v
.venv/bin/pytest -q
```

Expected: 2 new pass; full suite green.

- [ ] **Step 5: Commit**

```bash
git add patchfeld/agents/manager.py tests/test_direct_message_to_agent.py
git commit -m "feat(agents): AgentManager routes DirectMessageToAgent → session.send"
```

---

## Task 13 — End-to-end: ask_orchestrator round-trip

**Files:**
- Create: `tests/test_app_smoke_plan3.py`

The integration test for the new control loop. We can't easily exercise a real ask_orchestrator from the FakeSDKAdapter (it doesn't simulate MCP execution), so we drive the inbox directly to verify the path:

1. Spawn a child via the manager.
2. Get the child's inbox; register a request_id; publish `AgentRequestedUserInput`.
3. Verify the OrchestratorSession injects a synthetic UserMessageToOrchestrator into the orchestrator's stream (by subscribing to it).
4. Call the orchestrator's `respond_to_agent_request` tool with the request_id.
5. Verify the inbox future resolves to the response.

- [ ] **Step 1: Write the test**

```python
import asyncio

import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

from patchfeld.agents.fake_sdk_adapter import FakeSDKAdapter
from patchfeld.agents.manager import AgentManager
from patchfeld.events import (
    AgentRequestedUserInput,
    EventBus,
    UserMessageToOrchestrator,
)
from patchfeld.orchestrator.session import OrchestratorSession
from patchfeld.orchestrator.tools import build_orchestrator_tools


def _ok():
    return [
        AssistantMessage(content=[TextBlock(text="done")], model="fake-model"),
        ResultMessage(
            subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
            num_turns=1, session_id="fake", total_cost_usd=0.0,
            usage={"input_tokens": 1, "output_tokens": 1}, result="done",
        ),
    ]


@pytest.mark.asyncio
async def test_ask_orchestrator_round_trip(tmp_path):
    bus = EventBus()
    user_messages: list[UserMessageToOrchestrator] = []
    bus.subscribe(UserMessageToOrchestrator, user_messages.append)

    manager = AgentManager(
        cwd=tmp_path,
        bus=bus,
        adapter_factory=lambda: FakeSDKAdapter(scripts=[_ok()]),
    )
    orchestrator = OrchestratorSession(
        cwd=tmp_path,
        bus=bus,
        manager=manager,
        adapter=FakeSDKAdapter(scripts=[_ok()]),
    )
    await orchestrator.start()

    aid = await manager.spawn(name="alpha", prompt="say hi")
    await manager.wait_idle(aid)

    inbox = manager.get_inbox(aid)
    request_id = inbox.register()
    bus.publish(
        AgentRequestedUserInput(
            agent_id=aid, question="go/no-go?", request_id=request_id
        )
    )
    await asyncio.sleep(0)

    # The orchestrator session injected a synthetic user message describing
    # the question.
    assert any("go/no-go" in m.text for m in user_messages)
    assert any(request_id in m.text for m in user_messages)

    # The orchestrator (in production: the AI) calls respond_to_agent_request.
    # We invoke it directly here.
    _spawn, _list, _read, _send, _interrupt, _kill, respond = build_orchestrator_tools(manager)

    # Race the wait against the resolution.
    async def waiter():
        return await inbox.wait(request_id, timeout_s=1.0)
    waiter_task = asyncio.create_task(waiter())

    await respond({"agent_id": aid, "request_id": request_id, "response": "ship it"})

    answer = await waiter_task
    assert answer == "ship it"

    await orchestrator.stop()
```

- [ ] **Step 2: Run**

```bash
.venv/bin/pytest tests/test_app_smoke_plan3.py -v
.venv/bin/pytest -q
```

Expected: 1 new pass; full suite green.

If the synthetic UserMessageToOrchestrator assertion fails, that means Task 10's wiring isn't catching the event — debug that handler before fixing the test.

- [ ] **Step 3: Commit**

```bash
git add tests/test_app_smoke_plan3.py
git commit -m "test(app): plan-3 e2e — ask_orchestrator round-trip"
```

---

## Task 14 — Opt-in real-SDK smoke test

**Files:**
- Create: `tests/test_real_sdk_smoke.py`

Adds CI-skipped end-to-end coverage of the real SDK: spawn a child via the orchestrator and verify the child completes. Gated on `ANTHROPIC_API_KEY` so it never runs unintentionally.

- [ ] **Step 1: Implement**

Create `tests/test_real_sdk_smoke.py`:

```python
import os

import pytest

from patchfeld.agents.manager import AgentManager
from patchfeld.agents.sdk_adapter import RealSDKAdapter
from patchfeld.events import EventBus


pytestmark = pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="Requires ANTHROPIC_API_KEY for live SDK smoke test.",
)


@pytest.mark.asyncio
async def test_real_child_agent_completes(tmp_path):
    """Live test: spawn a child agent and let it run a single turn.

    Skipped unless ANTHROPIC_API_KEY is set. Costs a fraction of a cent."""
    bus = EventBus()
    manager = AgentManager(cwd=tmp_path, bus=bus, adapter_factory=RealSDKAdapter)
    agent_id = await manager.spawn(
        name="smoke",
        prompt="Reply with just the word 'hello' and nothing else.",
        allowed_tools=[],  # no tools needed; just text response
    )
    await manager.wait_idle(agent_id)

    info = next(i for i in manager.list_infos() if i.id == agent_id)
    assert info.state.is_terminal, f"agent did not reach terminal state: {info.state}"
    entries = manager.read_transcript(agent_id)
    assert any(e.role == "assistant" for e in entries)
    assert info.tokens_in > 0
    assert info.tokens_out > 0

    await manager.shutdown()
```

- [ ] **Step 2: Verify it skips gracefully**

```bash
cd /Users/jimmy.mills/Developer/patchfeld && .venv/bin/pytest tests/test_real_sdk_smoke.py -v
```

Expected (no API key set): `1 skipped`. Do NOT actually run with a key — leave that to the user.

```bash
.venv/bin/pytest -q
```

Expected: full suite green; the smoke test shows as skipped.

- [ ] **Step 3: Commit**

```bash
git add tests/test_real_sdk_smoke.py
git commit -m "test: opt-in real-SDK smoke test gated on ANTHROPIC_API_KEY"
```

---

## Task 15 — Manual launch verification + tag `plan-3-complete`

- [ ] **Step 1: Imports**

```bash
cd /Users/jimmy.mills/Developer/patchfeld && .venv/bin/python -c "
from patchfeld.app import PatchfeldApp
from patchfeld.agents.manager import AgentManager
from patchfeld.agents.child_tools import build_child_mcp_server
from patchfeld.agents.request_inbox import RequestInbox
print('plan 3 imports OK')
"
```

Expected: `plan 3 imports OK`.

- [ ] **Step 2: Full suite**

```bash
.venv/bin/pytest -v
```

Expected: every non-skipped test passes; the real-SDK smoke is skipped without an API key.

- [ ] **Step 3: Commit any leftover docs**

```bash
git status
```

If the plan doc isn't committed, add and commit it. Otherwise skip.

- [ ] **Step 4: Tag**

```bash
git tag plan-3-complete
git tag --list
```

Expected: tag list includes `walking-skeleton-complete`, `plan-2-complete`, `plan-3-complete`.

---

## Self-review notes (for the writer of this plan, already verified)

- **Spec coverage:** plan-3 brainstorming targets — `send_to_agent` (Task 9), `interrupt_agent` (Task 9), `kill_agent` (Task 9), `notify_orchestrator` (Task 7), `ask_orchestrator` (Task 7), direct-to-agent input (Tasks 11+12), serialize race (Tasks 4+5), opt-in real-SDK smoke (Task 14). All covered.
- **Placeholder scan:** no "TODO" / "TBD" / "implement later". Every step has actual code or commands.
- **Type consistency:** `RequestInbox`, `AgentNotifiedOrchestrator`, `DirectMessageToAgent`, `build_child_tools`, `build_child_mcp_server`, `_TOOL_SPECS` / `_SPECS`, `_send_handler` / `_interrupt_handler` / `_kill_handler` / `_respond_handler` — names used identically across all tasks.
- **Carried-forward gotchas:**
  - Plan-2's `bypassPermissions` stays. Replacing it with a Textual modal is plan 4.
  - The `@tool` decorator's `SdkMcpTool` wrapping — handled the same way as plan 2 (return `.handler`s; re-decorate in `build_orchestrator_mcp_server`).
  - The orchestrator's `_on_user_message` create_task pattern — Task 5 hardens `wait_idle`, Task 4 makes inner `send` safe under concurrent calls.
- **Tool count after plan 3:** orchestrator now has 7 tools (`spawn_agent`, `list_agents`, `read_agent_transcript`, `send_to_agent`, `interrupt_agent`, `kill_agent`, `respond_to_agent_request`). Children have 2 (`notify_orchestrator`, `ask_orchestrator`). Plan 4 adds the layout/config tools.
