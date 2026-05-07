import pytest
from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    TextBlock,
)

from mod_tui.agents.fake_sdk_adapter import FakeSDKAdapter
from mod_tui.agents.manager import AgentManager
from mod_tui.events import EventBus
from mod_tui.orchestrator.session import OrchestratorSession
from mod_tui.persistence.orchestrator_sessions import (
    OrchestratorSessionEntry,
    OrchestratorSessionsIndex,
)


def _ok_script(session_id: str = "s-fake"):
    return [
        AssistantMessage(content=[TextBlock(text="ok")], model="fake-model"),
        ResultMessage(
            subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
            num_turns=1, session_id=session_id, total_cost_usd=0.0,
            usage={"input_tokens": 1, "output_tokens": 1}, result="ok",
        ),
    ]


class _RecordingAdapter(FakeSDKAdapter):
    """FakeSDKAdapter that records the options it was started with."""

    def __init__(self, scripts):
        super().__init__(scripts)
        self.last_options = None

    async def start(self, *, options):
        self.last_options = options
        await super().start(options=options)


def _build_orch(tmp_path, *, adapter):
    bus = EventBus()
    manager = AgentManager(
        cwd=tmp_path, bus=bus,
        adapter_factory=lambda: FakeSDKAdapter(scripts=[]),
    )
    orch = OrchestratorSession(
        cwd=tmp_path, bus=bus, manager=manager, adapter=adapter,
    )
    return orch, bus


@pytest.mark.asyncio
async def test_start_with_no_prior_sessions_passes_fresh_session_id(tmp_path):
    adapter = _RecordingAdapter(scripts=[_ok_script()])
    orch, _ = _build_orch(tmp_path, adapter=adapter)
    await orch.start()
    try:
        assert adapter.last_options.resume is None
        assert adapter.last_options.session_id is not None
    finally:
        await orch.stop()


@pytest.mark.asyncio
async def test_start_with_prior_session_passes_resume(tmp_path):
    idx = OrchestratorSessionsIndex(cwd=tmp_path)
    idx.upsert(OrchestratorSessionEntry(
        session_id="prev-id", transcript_path="x.jsonl",
        started_at=100.0, last_activity=200.0,
    ))
    adapter = _RecordingAdapter(scripts=[_ok_script(session_id="prev-id")])
    orch, _ = _build_orch(tmp_path, adapter=adapter)
    await orch.start()
    try:
        assert adapter.last_options.resume == "prev-id"
    finally:
        await orch.stop()


@pytest.mark.asyncio
async def test_start_skips_legacy_entries_for_resume(tmp_path):
    idx = OrchestratorSessionsIndex(cwd=tmp_path)
    idx.upsert(OrchestratorSessionEntry(
        session_id="legacy-1", transcript_path="x.jsonl",
        started_at=100.0, last_activity=200.0, legacy=True,
    ))
    adapter = _RecordingAdapter(scripts=[_ok_script()])
    orch, _ = _build_orch(tmp_path, adapter=adapter)
    await orch.start()
    try:
        assert adapter.last_options.resume is None
    finally:
        await orch.stop()


@pytest.mark.asyncio
async def test_start_runs_legacy_migration(tmp_path):
    transcripts = tmp_path / ".mod_tui" / "transcripts"
    transcripts.mkdir(parents=True)
    legacy = transcripts / "orchestrator.jsonl"
    legacy.write_text('{"role": "user", "text": "old"}\n', encoding="utf-8")

    adapter = _RecordingAdapter(scripts=[_ok_script()])
    orch, _ = _build_orch(tmp_path, adapter=adapter)
    await orch.start()
    try:
        assert not legacy.exists()
        assert any(p.name.startswith("orchestrator.legacy-")
                   for p in transcripts.iterdir())
    finally:
        await orch.stop()


@pytest.mark.asyncio
async def test_active_transcript_path_reflects_active_session(tmp_path):
    adapter = _RecordingAdapter(scripts=[_ok_script(session_id="brand-new")])
    orch, _ = _build_orch(tmp_path, adapter=adapter)
    await orch.start()
    try:
        p = orch.active_transcript_path
        assert p is not None
        assert p.name.startswith("orchestrator.")
        assert p.suffix == ".jsonl"
    finally:
        await orch.stop()


@pytest.mark.asyncio
async def test_first_result_message_upserts_index(tmp_path):
    adapter = _RecordingAdapter(scripts=[_ok_script(session_id="confirmed-id")])
    orch, _ = _build_orch(tmp_path, adapter=adapter)
    await orch.start()
    try:
        # Send one message so a ResultMessage flows.
        from mod_tui.events import UserMessageToOrchestrator
        orch._bus.publish(UserMessageToOrchestrator("hi"))
        await orch.wait_idle()

        idx = OrchestratorSessionsIndex(cwd=tmp_path)
        entries = idx.list()
        ids = {e.session_id for e in entries}
        assert "confirmed-id" in ids
        entry = idx.get("confirmed-id")
        assert entry is not None
        assert entry.legacy is False
    finally:
        await orch.stop()
