import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

from patchbai.agents.fake_sdk_adapter import FakeSDKAdapter
from patchbai.agents.manager import AgentManager
from patchbai.events import EventBus, UserMessageToOrchestrator
from patchbai.orchestrator.session import OrchestratorSession


class _RecordingAdapter(FakeSDKAdapter):
    def __init__(self, scripts):
        super().__init__(scripts)
        self.last_options = None

    async def start(self, *, options):
        self.last_options = options
        await super().start(options=options)


def _script(sid: str):
    return [
        AssistantMessage(content=[TextBlock(text="ok")], model="fake-model"),
        ResultMessage(
            subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
            num_turns=1, session_id=sid, total_cost_usd=0.0,
            usage={"input_tokens": 1, "output_tokens": 1}, result="ok",
        ),
    ]


@pytest.mark.asyncio
async def test_orchestrator_auto_resumes_across_restart(tmp_path):
    bus1 = EventBus()
    manager1 = AgentManager(
        cwd=tmp_path, bus=bus1,
        adapter_factory=lambda: FakeSDKAdapter(scripts=[]),
    )
    adapter1 = _RecordingAdapter(scripts=[_script("seedling")])
    orch1 = OrchestratorSession(
        cwd=tmp_path, bus=bus1, manager=manager1, adapter=adapter1,
    )
    await orch1.start()
    bus1.publish(UserMessageToOrchestrator("hi from session 1"))
    await orch1.wait_idle()
    await orch1.stop()

    # Simulate a fresh app process by constructing a new orchestrator and
    # bus at the same cwd.
    bus2 = EventBus()
    manager2 = AgentManager(
        cwd=tmp_path, bus=bus2,
        adapter_factory=lambda: FakeSDKAdapter(scripts=[]),
    )
    adapter2 = _RecordingAdapter(scripts=[_script("seedling")])
    orch2 = OrchestratorSession(
        cwd=tmp_path, bus=bus2, manager=manager2, adapter=adapter2,
    )
    await orch2.start()
    try:
        assert adapter2.last_options.resume == "seedling"
    finally:
        await orch2.stop()
