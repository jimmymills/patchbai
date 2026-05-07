from pathlib import Path

import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

from patchbai.agents.fake_sdk_adapter import FakeSDKAdapter
from patchbai.agents.manager import AgentManager
from patchbai.app import PatchbaiApp
from patchbai.events import AgentTokensTouched, EventBus, StatsUpdated
from patchbai.orchestrator.session import OrchestratorSession


def _ok_with_usage(tokens_in: int, tokens_out: int, cost: float = 0.0) -> list:
    return [
        AssistantMessage(content=[TextBlock(text="ack")], model="fake-model"),
        ResultMessage(
            subtype="success",
            duration_ms=1, duration_api_ms=1, is_error=False, num_turns=1,
            session_id="fake", total_cost_usd=cost,
            usage={"input_tokens": tokens_in, "output_tokens": tokens_out},
            result="ack",
        ),
    ]


def _build_test_app(tmp_path: Path, *, orch_script: list | None = None) -> PatchbaiApp:
    bus = EventBus()
    manager = AgentManager(
        cwd=tmp_path,
        bus=bus,
        adapter_factory=lambda: FakeSDKAdapter(scripts=[_ok_with_usage(0, 0)]),
    )
    orchestrator = OrchestratorSession(
        cwd=tmp_path,
        bus=bus,
        manager=manager,
        adapter=FakeSDKAdapter(scripts=[orch_script or _ok_with_usage(0, 0)]),
    )
    app = PatchbaiApp(cwd=tmp_path, manager=manager, orchestrator=orchestrator)
    app.event_bus = bus
    return app


@pytest.mark.asyncio
async def test_orchestrator_token_increment_fires_stats_updated(tmp_path: Path):
    """Sending a message through the orchestrator should accumulate tokens
    on its AgentInfo and result in a StatsUpdated event hitting the bus."""
    script = _ok_with_usage(tokens_in=42, tokens_out=17, cost=0.005)
    app = _build_test_app(tmp_path, orch_script=script)

    captured: list[StatsUpdated] = []

    async with app.run_test() as pilot:
        await pilot.pause()
        # Subscribe AFTER mount so we don't capture transient zeroes.
        app.event_bus.subscribe(StatsUpdated, lambda e: captured.append(e))
        # Drive a message through the orchestrator. The fake adapter then
        # replays the scripted ResultMessage, AgentSession increments info,
        # publishes AgentTokensTouched, the app aggregator publishes StatsUpdated.
        from patchbai.events import UserMessageToOrchestrator
        app.event_bus.publish(UserMessageToOrchestrator(text="hi"))
        # Give the orchestrator's send task time to drain the script.
        await app.orchestrator.wait_idle()
        await pilot.pause()

        assert any(
            e.tokens_in >= 42 and e.tokens_out >= 17 and e.cost >= 0.005
            for e in captured
        ), f"no StatsUpdated reflected the orchestrator's usage; got {captured}"


@pytest.mark.asyncio
async def test_stats_updated_aggregates_orchestrator_and_children(tmp_path: Path):
    """Manually seed orchestrator + child token counters and fire
    AgentTokensTouched — the aggregator must sum them in StatsUpdated."""
    app = _build_test_app(tmp_path)
    captured: list[StatsUpdated] = []

    async with app.run_test() as pilot:
        await pilot.pause()
        app.event_bus.subscribe(StatsUpdated, lambda e: captured.append(e))

        # Seed orchestrator counters directly.
        app.orchestrator.info.tokens_in = 100
        app.orchestrator.info.tokens_out = 50
        app.orchestrator.info.cost = 0.01

        # Trigger aggregation. AgentTokensTouched is a signal; the aggregator
        # re-reads canonical AgentInfo objects, so the agent_id field can be
        # any value — we're not modeling real children here.
        app.event_bus.publish(AgentTokensTouched(agent_id="orchestrator"))
        await pilot.pause()

        assert captured, "aggregator did not publish StatsUpdated"
        latest = captured[-1]
        assert latest.tokens_in == 100
        assert latest.tokens_out == 50
        assert latest.cost == pytest.approx(0.01)


@pytest.mark.asyncio
async def test_aggregator_handles_no_children(tmp_path: Path):
    """No spawned agents → active_agents must be 0 and aggregation should
    proceed using only orchestrator counters."""
    app = _build_test_app(tmp_path)
    captured: list[StatsUpdated] = []

    async with app.run_test() as pilot:
        await pilot.pause()
        app.event_bus.subscribe(StatsUpdated, lambda e: captured.append(e))
        app.event_bus.publish(AgentTokensTouched(agent_id="orchestrator"))
        await pilot.pause()
        assert captured[-1].active_agents == 0
