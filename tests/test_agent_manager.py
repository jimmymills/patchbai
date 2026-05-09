from pathlib import Path

import pytest
from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    TextBlock,
)

from patchfeld.agents.fake_sdk_adapter import FakeSDKAdapter
from patchfeld.agents.manager import AgentManager
from patchfeld.agents.state import AgentState
from patchfeld.events import AgentArchiveChanged, AgentSpawned, EventBus
from patchfeld.persistence.agents_index import AgentsIndex


def _ok_script() -> list:
    return [
        AssistantMessage(content=[TextBlock(text="done")], model="fake-model"),
        ResultMessage(
            subtype="success",
            duration_ms=10,
            duration_api_ms=5,
            is_error=False,
            num_turns=1,
            session_id="fake-session",
            total_cost_usd=0.0,
            usage={"input_tokens": 1, "output_tokens": 1},
            result="done",
        ),
    ]


@pytest.mark.asyncio
async def test_spawn_returns_agent_id_and_emits_spawned_event(tmp_path: Path):
    bus = EventBus()
    spawned: list[AgentSpawned] = []
    bus.subscribe(AgentSpawned, spawned.append)

    manager = AgentManager(
        cwd=tmp_path,
        bus=bus,
        adapter_factory=lambda: FakeSDKAdapter(scripts=[_ok_script()]),
    )
    agent_id = await manager.spawn(name="research", prompt="say done")

    assert isinstance(agent_id, str) and agent_id
    assert len(spawned) == 1
    assert spawned[0].info.id == agent_id
    assert spawned[0].info.name == "research"


@pytest.mark.asyncio
async def test_spawn_persists_to_agents_index(tmp_path: Path):
    manager = AgentManager(
        cwd=tmp_path,
        bus=EventBus(),
        adapter_factory=lambda: FakeSDKAdapter(scripts=[_ok_script()]),
    )
    await manager.spawn(name="research", prompt="say done")
    assert (tmp_path / ".patchfeld" / "agents.json").exists()


@pytest.mark.asyncio
async def test_list_infos_returns_current_state(tmp_path: Path):
    manager = AgentManager(
        cwd=tmp_path,
        bus=EventBus(),
        adapter_factory=lambda: FakeSDKAdapter(scripts=[_ok_script()]),
    )
    aid = await manager.spawn(name="research", prompt="say done")
    await manager.wait_idle(aid)

    infos = manager.list_infos()
    assert len(infos) == 1
    assert infos[0].state == AgentState.DONE


@pytest.mark.asyncio
async def test_read_transcript_returns_recorded_entries(tmp_path: Path):
    manager = AgentManager(
        cwd=tmp_path,
        bus=EventBus(),
        adapter_factory=lambda: FakeSDKAdapter(scripts=[_ok_script()]),
    )
    aid = await manager.spawn(name="research", prompt="say done")
    await manager.wait_idle(aid)

    entries = manager.read_transcript(aid)
    roles = [e.role for e in entries]
    texts = [e.text for e in entries]
    assert "user" in roles and "assistant" in roles
    assert "say done" in texts and "done" in texts


@pytest.mark.asyncio
async def test_kill_removes_session(tmp_path: Path):
    manager = AgentManager(
        cwd=tmp_path,
        bus=EventBus(),
        adapter_factory=lambda: FakeSDKAdapter(scripts=[_ok_script()]),
    )
    aid = await manager.spawn(name="research", prompt="say done")
    await manager.wait_idle(aid)
    await manager.kill(aid)
    assert manager.get_session(aid) is None


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
async def test_set_archived_flips_flag_persists_and_publishes(tmp_path: Path):
    bus = EventBus()
    events: list[AgentArchiveChanged] = []
    bus.subscribe(AgentArchiveChanged, events.append)

    manager = AgentManager(
        cwd=tmp_path,
        bus=bus,
        adapter_factory=lambda: FakeSDKAdapter(scripts=[_ok_script()]),
    )
    aid = await manager.spawn(name="research", prompt="say done")
    await manager.wait_idle(aid)

    manager.set_archived(aid, archived=True)

    # In-memory info reflects the change.
    [info] = [i for i in manager.list_infos() if i.id == aid]
    assert info.archived is True
    # Persistence reflects the change too — survives a restart.
    persisted = AgentsIndex(cwd=tmp_path).load()
    assert any(p.id == aid and p.archived for p in persisted)
    # An event was published so subscribers (e.g., AgentTable) can refresh.
    assert events and events[-1].info.id == aid and events[-1].info.archived is True

    # Toggling back un-archives.
    manager.set_archived(aid, archived=False)
    [info2] = [i for i in manager.list_infos() if i.id == aid]
    assert info2.archived is False
    persisted2 = AgentsIndex(cwd=tmp_path).load()
    assert any(p.id == aid and not p.archived for p in persisted2)


@pytest.mark.asyncio
async def test_set_archived_unknown_id_raises_keyerror(tmp_path: Path):
    manager = AgentManager(
        cwd=tmp_path,
        bus=EventBus(),
        adapter_factory=lambda: FakeSDKAdapter(scripts=[_ok_script()]),
    )
    with pytest.raises(KeyError):
        manager.set_archived("nope", archived=True)


@pytest.mark.asyncio
async def test_set_archived_works_for_persisted_agent_without_live_session(
    tmp_path: Path,
):
    # An agent persisted by a previous process appears in the AgentTable
    # (seeded from agents.json on mount) but has no entry in
    # AgentManager._sessions. Pressing `d` on such a row used to crash with
    # KeyError; archive must operate on the persisted record instead.
    from patchfeld.agents.state import AgentInfo, AgentState
    from patchfeld.persistence.agents_index import AgentsIndex

    AgentsIndex(cwd=tmp_path).save([
        AgentInfo(id="ghost", name="lister", cwd=str(tmp_path),
                  started_at=100.0, state=AgentState.DONE, ended_at=200.0),
    ])

    bus = EventBus()
    events: list[AgentArchiveChanged] = []
    bus.subscribe(AgentArchiveChanged, events.append)

    manager = AgentManager(
        cwd=tmp_path, bus=bus,
        adapter_factory=lambda: FakeSDKAdapter(scripts=[_ok_script()]),
    )
    assert manager.get_session("ghost") is None

    manager.set_archived("ghost", archived=True)

    persisted = next(i for i in AgentsIndex(cwd=tmp_path).load() if i.id == "ghost")
    assert persisted.archived is True
    assert events and events[-1].info.id == "ghost"
    assert events[-1].info.archived is True

    # Toggling back un-archives.
    manager.set_archived("ghost", archived=False)
    persisted2 = next(i for i in AgentsIndex(cwd=tmp_path).load() if i.id == "ghost")
    assert persisted2.archived is False


@pytest.mark.asyncio
async def test_spawn_captures_session_id_and_spawn_options(tmp_path: Path):
    # Resume across restarts depends on (a) spawn_options being persisted
    # at spawn time and (b) the SDK session_id being captured from the first
    # ResultMessage. Verify both land on disk.
    from patchfeld.persistence.agents_index import AgentsIndex

    manager = AgentManager(
        cwd=tmp_path,
        bus=EventBus(),
        adapter_factory=lambda: FakeSDKAdapter(scripts=[_ok_script()]),
    )
    aid = await manager.spawn(
        name="research", prompt="hi",
        model="claude-sonnet-4-6", allowed_tools=["Read"],
    )
    await manager.wait_idle(aid)

    persisted = next(i for i in AgentsIndex(cwd=tmp_path).load() if i.id == aid)
    assert persisted.session_id == "fake-session"
    assert persisted.spawn_options is not None
    assert persisted.spawn_options["model"] == "claude-sonnet-4-6"
    assert persisted.spawn_options["allowed_tools"] == ["Read"]


@pytest.mark.asyncio
async def test_resume_revives_session_for_persisted_agent(tmp_path: Path):
    # Simulate a crash-restart: spawn in one manager, drop it, then construct
    # a fresh manager (mirrors a fresh process) and call resume(). The
    # resumed session should be live and reachable via send().
    bus = EventBus()
    m1 = AgentManager(
        cwd=tmp_path, bus=bus,
        adapter_factory=lambda: FakeSDKAdapter(scripts=[_ok_script()]),
    )
    aid = await m1.spawn(name="research", prompt="first")
    await m1.wait_idle(aid)
    await m1.shutdown()

    m2 = AgentManager(
        cwd=tmp_path, bus=EventBus(),
        adapter_factory=lambda: FakeSDKAdapter(scripts=[_ok_script(), _ok_script()]),
    )
    # Pre-resume the manager has no live session for this id.
    assert m2.get_session(aid) is None

    revived = await m2.resume(aid)
    assert revived is not None
    assert m2.get_session(aid) is revived

    # Now send works against the revived session.
    await m2.send(aid, "follow up")
    await m2.wait_idle(aid)


@pytest.mark.asyncio
async def test_resume_returns_none_for_legacy_record(tmp_path: Path):
    # Records written before the resume feature have no session_id /
    # spawn_options. resume() must report this and not throw.
    from patchfeld.agents.state import AgentInfo, AgentState
    from patchfeld.persistence.agents_index import AgentsIndex

    AgentsIndex(cwd=tmp_path).save([
        AgentInfo(id="legacy", name="old", cwd=str(tmp_path),
                  started_at=100.0, state=AgentState.ERROR),
    ])
    manager = AgentManager(
        cwd=tmp_path, bus=EventBus(),
        adapter_factory=lambda: FakeSDKAdapter(scripts=[_ok_script()]),
    )
    assert await manager.resume("legacy") is None


@pytest.mark.asyncio
async def test_direct_message_lazily_resumes_dead_agent(tmp_path: Path):
    import asyncio

    from patchfeld.events import DirectMessageToAgent

    bus = EventBus()
    m1 = AgentManager(
        cwd=tmp_path, bus=bus,
        adapter_factory=lambda: FakeSDKAdapter(scripts=[_ok_script()]),
    )
    aid = await m1.spawn(name="research", prompt="first")
    await m1.wait_idle(aid)
    await m1.shutdown()

    bus2 = EventBus()
    m2 = AgentManager(
        cwd=tmp_path, bus=bus2,
        adapter_factory=lambda: FakeSDKAdapter(scripts=[_ok_script(), _ok_script()]),
    )
    bus2.publish(DirectMessageToAgent(agent_id=aid, text="hello again"))
    # Let the resume task scheduled by the handler run to completion.
    for _ in range(10):
        await asyncio.sleep(0)
        if m2.get_session(aid) is not None:
            break
    await m2.wait_idle(aid)

    entries = m2.read_transcript(aid)
    user_texts = [e.text for e in entries if e.role == "user"]
    # First prompt is from the prior process; the second is the lazy-resume
    # send we just published.
    assert user_texts == ["first", "hello again"]


class _RecordingAdapter(FakeSDKAdapter):
    """FakeSDKAdapter that exposes the last ClaudeAgentOptions it was started
    with, so tests can assert on what AgentManager passed to the SDK."""

    def __init__(self, scripts):
        super().__init__(scripts)
        self.last_options = None

    async def start(self, *, options):
        self.last_options = options
        await super().start(options=options)


@pytest.mark.asyncio
async def test_spawn_appends_orchestrator_comms_guidance_to_system_prompt(
    tmp_path: Path,
):
    """Newly spawned children must be told about the patchfeld_child MCP
    tools (notify_orchestrator + ask_orchestrator) so they actually use
    them. The guidance is appended to the default Claude Code preset so
    the rest of the CLI behavior stays intact."""
    adapter = _RecordingAdapter(scripts=[_ok_script()])
    manager = AgentManager(
        cwd=tmp_path,
        bus=EventBus(),
        adapter_factory=lambda: adapter,
    )
    await manager.spawn(name="research", prompt="hi")

    sp = adapter.last_options.system_prompt
    assert isinstance(sp, dict), f"expected SystemPromptPreset dict, got {sp!r}"
    assert sp.get("type") == "preset"
    assert sp.get("preset") == "claude_code"
    append = sp.get("append") or ""
    # Both child tools called out by name so the model recognises them.
    assert "notify_orchestrator" in append
    assert "ask_orchestrator" in append
    # The MCP server name is mentioned (helps the model route).
    assert "patchfeld_child" in append
    # Anti-spam / milestone guidance is present.
    assert "milestone" in append.lower() or "spam" in append.lower()
    # The 300s timeout behavior is documented so children don't loop.
    assert "300" in append or "timeout" in append.lower()


@pytest.mark.asyncio
async def test_spawn_user_system_prompt_does_not_drop_orchestrator_guidance(
    tmp_path: Path,
):
    """If a caller passes their own system_prompt to spawn(), the
    orchestrator-comms guidance must STILL reach the child — it's never
    silently replaced. The caller's content is combined with the guidance
    inside the same SystemPromptPreset.append, so neither is lost."""
    adapter = _RecordingAdapter(scripts=[_ok_script()])
    manager = AgentManager(
        cwd=tmp_path,
        bus=EventBus(),
        adapter_factory=lambda: adapter,
    )
    await manager.spawn(
        name="r",
        prompt="hi",
        system_prompt="You are an obscure expert in foo.",
    )

    sp = adapter.last_options.system_prompt
    assert isinstance(sp, dict)
    assert sp.get("type") == "preset"
    assert sp.get("preset") == "claude_code"
    append = sp.get("append") or ""
    # Caller's instructions are preserved verbatim.
    assert "obscure expert in foo" in append
    # The orchestrator-comms guidance is still injected alongside.
    assert "notify_orchestrator" in append
    assert "ask_orchestrator" in append


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


@pytest.mark.asyncio
async def test_inbox_register_flips_session_to_waiting_and_back(tmp_path):
    from patchfeld.agents.fake_sdk_adapter import FakeSDKAdapter
    from patchfeld.agents.manager import AgentManager
    from patchfeld.agents.state import AgentState
    from patchfeld.events import AgentStateChanged, EventBus
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
