import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

from patchbai.agents.fake_sdk_adapter import FakeSDKAdapter
from patchbai.agents.manager import AgentManager
from patchbai.app import PatchbaiApp
from patchbai.events import EventBus
from patchbai.orchestrator.session import OrchestratorSession
from patchbai.orchestrator.tools import build_orchestrator_tools
from patchbai.widgets.markdown import Markdown


def _ok():
    return [
        AssistantMessage(content=[TextBlock(text="ok")], model="fake-model"),
        ResultMessage(
            subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
            num_turns=1, session_id="fake", total_cost_usd=0.0,
            usage={"input_tokens": 1, "output_tokens": 1}, result="ok",
        ),
    ]


@pytest.mark.asyncio
async def test_orchestrator_can_set_layout_with_markdown_panel(tmp_path):
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
        widget_registry=app.registry,
    )

    async with app.run_test() as pilot:
        await pilot.pause()

        tools = build_orchestrator_tools(
            app.manager,
            apply_layout=app._orchestrator_apply_layout,
            layouts_store=app.layouts_store,
            config_store=app.config_store,
            actions=app.actions_registry,
            rebind_keys=app._rebind_keys,
            widget_registry=app.registry,
        )

        spec = {
            "version": 1,
            "layout": {
                "type": "horizontal",
                "children": [
                    {"id": "orch", "size": "60%", "widget": "OrchestratorChat"},
                    {
                        "id": "doc", "size": "40%",
                        "widget": "Markdown",
                        "props": {"source": "# Plan 5\n\nMarkdown panel works."},
                    },
                ],
            },
            "focus": "orch",
        }
        out = await tools["set_layout"]({"spec": spec})
        assert "applied" in out["content"][0]["text"].lower()
        await pilot.pause()

        md = app.query_one(Markdown)
        assert "Plan 5" in md._markdown
