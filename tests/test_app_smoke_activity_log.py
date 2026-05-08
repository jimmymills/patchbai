import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

from patchbai.activity.log import ActivityLog
from patchbai.agents.fake_sdk_adapter import FakeSDKAdapter
from patchbai.agents.manager import AgentManager
from patchbai.app import PatchbaiApp
from patchbai.events import EventBus, TabAdded


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
async def test_app_init_creates_activity_log(tmp_path):
    """PatchbaiApp.__init__ must instantiate self.activity_log so that no
    caller has to remember to do it."""
    bus = EventBus()
    manager = AgentManager(
        cwd=tmp_path, bus=bus,
        adapter_factory=lambda: FakeSDKAdapter(scripts=[_ok()]),
    )
    app = PatchbaiApp(cwd=tmp_path, manager=manager, global_dir=tmp_path)
    assert isinstance(app.activity_log, ActivityLog)


@pytest.mark.asyncio
async def test_activity_log_is_subscribed_to_app_event_bus(tmp_path):
    """The ActivityLog created in __init__ must be wired to app.event_bus."""
    bus = EventBus()
    manager = AgentManager(
        cwd=tmp_path, bus=bus,
        adapter_factory=lambda: FakeSDKAdapter(scripts=[_ok()]),
    )
    app = PatchbaiApp(cwd=tmp_path, manager=manager, global_dir=tmp_path)
    # Use the app's own event_bus (not the manager-passed bus, which the
    # smoke fixture rebinds in other tests but we leave alone here).
    app.event_bus.publish(TabAdded(tab_id="t1", title="Files"))
    entries = app.activity_log.entries()
    assert any(e.kind == "tab.added" and e.tab_id == "t1" for e in entries)
