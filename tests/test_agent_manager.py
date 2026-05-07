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
from mod_tui.events import AgentArchiveChanged, AgentSpawned, EventBus
from mod_tui.persistence.agents_index import AgentsIndex


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
