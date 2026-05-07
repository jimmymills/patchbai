from pathlib import Path

import pytest
from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    TextBlock,
)

from mod_tui.agents.fake_sdk_adapter import FakeSDKAdapter
from mod_tui.agents.manager import AgentManager
from mod_tui.agents.state import AgentState
from mod_tui.events import AgentSpawned, EventBus


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
    assert (tmp_path / ".mod_tui" / "agents.json").exists()


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
async def test_get_inbox_returns_a_request_inbox_per_agent(tmp_path: Path):
    from mod_tui.agents.request_inbox import RequestInbox

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
    from mod_tui.agents.fake_sdk_adapter import FakeSDKAdapter
    from mod_tui.agents.manager import AgentManager
    from mod_tui.agents.state import AgentState
    from mod_tui.events import AgentStateChanged, EventBus
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
