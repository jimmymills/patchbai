import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

from patchbai.agents.fake_sdk_adapter import FakeSDKAdapter
from patchbai.agents.manager import AgentManager
from patchbai.app import PatchbaiApp
from patchbai.events import EventBus
from patchbai.orchestrator.session import OrchestratorSession
from patchbai.orchestrator.tools import build_orchestrator_tools


def _ok():
    return [
        AssistantMessage(content=[TextBlock(text="ok")], model="fake-model"),
        ResultMessage(
            subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
            num_turns=1, session_id="fake", total_cost_usd=0.0,
            usage={"input_tokens": 1, "output_tokens": 1}, result="ok",
        ),
    ]


def _build_app(tmp_path):
    bus = EventBus()
    manager = AgentManager(
        cwd=tmp_path, bus=bus,
        adapter_factory=lambda: FakeSDKAdapter(scripts=[_ok()]),
    )
    app = PatchbaiApp(cwd=tmp_path, manager=manager, global_dir=tmp_path)
    app.event_bus = bus

    app.orchestrator = OrchestratorSession(
        cwd=tmp_path, bus=bus, manager=manager,
        adapter=FakeSDKAdapter(scripts=[_ok()]),
        apply_layout=app._orchestrator_apply_layout,
        layouts_store=app.layouts_store,
        config_store=app.config_store,
        actions=app.actions_registry,
        rebind_keys=app._rebind_keys,
    )
    return app


@pytest.mark.asyncio
async def test_bind_key_via_tool_then_press_triggers_action(tmp_path):
    app = _build_app(tmp_path)
    invocations: list[str] = []

    # Replace one registered action with a spy.
    app.actions_registry.register(
        "focus_orchestrator",
        lambda: invocations.append("focused"),
        description="spy", args_schema={},
    )

    async with app.run_test() as pilot:
        await pilot.pause()

        # Use the orchestrator's bind_key tool to bind ~ to focus_orchestrator.
        tools = build_orchestrator_tools(
            app.manager,
            apply_layout=app._orchestrator_apply_layout,
            layouts_store=app.layouts_store,
            config_store=app.config_store,
            actions=app.actions_registry,
            rebind_keys=app._rebind_keys,
        )
        bind_key = tools["bind_key"]
        await bind_key({"key": "~", "action": "focus_orchestrator"})
        await pilot.pause()

        # Press the newly-bound key.
        await pilot.press("~")
        await pilot.pause()

    assert invocations == ["focused"]
