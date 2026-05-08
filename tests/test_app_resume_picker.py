import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

from patchfeld.agents.fake_sdk_adapter import FakeSDKAdapter
from patchfeld.agents.manager import AgentManager
from patchfeld.app import PatchfeldApp
from patchfeld.events import EventBus, OpenResumePicker
from patchfeld.orchestrator.session import OrchestratorSession
from patchfeld.persistence.orchestrator_sessions import (
    OrchestratorSessionEntry,
    OrchestratorSessionsIndex,
)
from patchfeld.widgets.resume_screen import ResumeScreen


def _ok():
    return [
        AssistantMessage(content=[TextBlock(text="ok")], model="fake-model"),
        ResultMessage(
            subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
            num_turns=1, session_id="boot", total_cost_usd=0.0,
            usage={"input_tokens": 1, "output_tokens": 1}, result="ok",
        ),
    ]


@pytest.mark.asyncio
async def test_open_resume_picker_pushes_resume_screen(tmp_path):
    # Seed an index entry so the picker has at least one row.
    OrchestratorSessionsIndex(cwd=tmp_path).upsert(OrchestratorSessionEntry(
        session_id="entry-a", transcript_path="x.jsonl",
        started_at=100.0, last_activity=200.0,
    ))

    bus = EventBus()
    manager = AgentManager(
        cwd=tmp_path, bus=bus,
        adapter_factory=lambda: FakeSDKAdapter(scripts=[]),
    )
    app = PatchfeldApp(cwd=tmp_path, manager=manager, global_dir=tmp_path)
    app.event_bus = bus
    app.orchestrator = OrchestratorSession(
        cwd=tmp_path, bus=bus, manager=manager,
        adapter=FakeSDKAdapter(scripts=[_ok()]),
    )

    async with app.run_test() as pilot:
        await pilot.pause()
        bus.publish(OpenResumePicker())
        await pilot.pause()
        assert isinstance(app.screen, ResumeScreen)
