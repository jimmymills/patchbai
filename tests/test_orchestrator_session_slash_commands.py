"""Integration tests for /bypass-permissions and /require-permissions slash commands."""
import asyncio
import pytest

from patchbai.app import PatchbaiApp
from patchbai.events import OrchestratorReply, UserMessageToOrchestrator


async def _wait_for_reply(replies: list, keyword: str, timeout: float = 5.0) -> bool:
    """Poll until a reply containing `keyword` appears or timeout expires."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if any(keyword in r.text.lower() for r in replies):
            return True
        await asyncio.sleep(0.05)
    return False


@pytest.mark.asyncio
async def test_slash_bypass_permissions_publishes_reply(tmp_path):
    """/bypass-permissions publishes a reply mentioning 'bypass'."""
    app = PatchbaiApp(cwd=tmp_path, global_dir=tmp_path / "cfg")
    replies: list[OrchestratorReply] = []
    app.event_bus.subscribe(OrchestratorReply, replies.append)

    async with app.run_test() as pilot:
        await pilot.pause()
        assert app._permission_grants is not None

        old_orch = app.orchestrator
        app.event_bus.publish(UserMessageToOrchestrator("/bypass-permissions"))

        # Wait for the send task (set_bypass_permissions) to complete.
        await old_orch.wait_idle()
        await pilot.pause()

        assert any("bypass" in r.text.lower() for r in replies)


@pytest.mark.asyncio
async def test_slash_require_permissions_publishes_reply(tmp_path):
    """/require-permissions publishes a reply mentioning 'require'."""
    app = PatchbaiApp(
        cwd=tmp_path, global_dir=tmp_path / "cfg",
        bypass_permissions=True,
    )
    replies: list[OrchestratorReply] = []
    app.event_bus.subscribe(OrchestratorReply, replies.append)

    async with app.run_test() as pilot:
        await pilot.pause()
        assert app._permission_grants is None

        old_orch = app.orchestrator
        app.event_bus.publish(UserMessageToOrchestrator("/require-permissions"))

        await old_orch.wait_idle()
        await pilot.pause()

        assert any("require" in r.text.lower() for r in replies)


@pytest.mark.asyncio
async def test_slash_bypass_permissions_already_bypassed(tmp_path):
    """/bypass-permissions when already bypassed replies with 'already'."""
    app = PatchbaiApp(
        cwd=tmp_path, global_dir=tmp_path / "cfg",
        bypass_permissions=True,
    )
    replies: list[OrchestratorReply] = []
    app.event_bus.subscribe(OrchestratorReply, replies.append)

    async with app.run_test() as pilot:
        await pilot.pause()
        old_orch = app.orchestrator
        app.event_bus.publish(UserMessageToOrchestrator("/bypass-permissions"))
        await old_orch.wait_idle()
        await pilot.pause()

        # Should reply "already" (unchanged).
        assert any("already" in r.text.lower() for r in replies)
        # Mode unchanged.
        assert app._permission_grants is None


@pytest.mark.asyncio
async def test_slash_bypass_permissions_refused_with_running_agents(tmp_path):
    """/bypass-permissions is refused when child agents are running."""
    from patchbai.agents.fake_sdk_adapter import FakeSDKAdapter
    from patchbai.agents.state import AgentState
    from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

    def _ok():
        return [
            AssistantMessage(content=[TextBlock(text="ok")], model="m"),
            ResultMessage(
                subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
                num_turns=1, session_id="s", total_cost_usd=0.0,
                usage={"input_tokens": 1, "output_tokens": 1}, result="ok",
            ),
        ]

    app = PatchbaiApp(cwd=tmp_path, global_dir=tmp_path / "cfg")
    replies: list[OrchestratorReply] = []
    app.event_bus.subscribe(OrchestratorReply, replies.append)

    async with app.run_test() as pilot:
        await pilot.pause()
        # Use a never-finishing adapter so the agent stays in RUNNING state.
        app.manager._adapter_factory = lambda: FakeSDKAdapter(scripts=[[
            AssistantMessage(content=[TextBlock(text="working...")], model="m"),
            # No ResultMessage: the stream never ends → agent stays RUNNING.
        ]])
        aid = await app.manager.spawn(name="worker", prompt="hi")
        # Give the stream task a chance to start and set state to RUNNING.
        await pilot.pause()
        # Ensure state is RUNNING (set explicitly as a backstop).
        app.manager.get_session(aid).info.state = AgentState.RUNNING

        old_orch = app.orchestrator
        app.event_bus.publish(UserMessageToOrchestrator("/bypass-permissions"))
        await old_orch.wait_idle()
        await pilot.pause()

        # Mode unchanged — still has grants.
        assert app._permission_grants is not None
        # Reply should mention the refusal.
        assert any(
            "refusing" in r.text.lower() or "running" in r.text.lower()
            for r in replies
        )
