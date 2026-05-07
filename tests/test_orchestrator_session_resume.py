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


@pytest.mark.asyncio
async def test_reset_does_not_send_literal_to_sdk(tmp_path):
    """Sending '/reset' must not appear as a prompt to the SDK."""
    adapter = _RecordingAdapter(scripts=[_ok_script()])
    # Second adapter for the new session after /reset.
    new_adapter = _RecordingAdapter(scripts=[_ok_script(session_id="post-reset")])

    bus = EventBus()
    manager = AgentManager(
        cwd=tmp_path, bus=bus,
        adapter_factory=lambda: FakeSDKAdapter(scripts=[]),
    )
    orch = OrchestratorSession(
        cwd=tmp_path, bus=bus, manager=manager, adapter=adapter,
    )
    # Inject the next adapter the orchestrator should use after reset.
    orch._next_adapter_factory = lambda: new_adapter

    await orch.start()
    try:
        from mod_tui.events import UserMessageToOrchestrator
        bus.publish(UserMessageToOrchestrator("/reset"))
        await orch.wait_idle()

        # The first adapter must not have been queried with "/reset".
        assert adapter._next_query_index == 0
        # The orchestrator's active session changed.
        assert orch._sdk_session_id != "s-fake"
    finally:
        await orch.stop()


@pytest.mark.asyncio
async def test_open_resume_picker_published_on_bare_resume(tmp_path):
    from mod_tui.events import OpenResumePicker, UserMessageToOrchestrator

    adapter = _RecordingAdapter(scripts=[_ok_script()])
    orch, bus = _build_orch(tmp_path, adapter=adapter)
    seen: list[OpenResumePicker] = []
    bus.subscribe(OpenResumePicker, seen.append)

    await orch.start()
    try:
        bus.publish(UserMessageToOrchestrator("/resume"))
        await orch.wait_idle()
        assert len(seen) == 1
    finally:
        await orch.stop()


@pytest.mark.asyncio
async def test_unknown_slash_command_falls_through_to_sdk(tmp_path):
    adapter = _RecordingAdapter(scripts=[_ok_script(), _ok_script()])
    orch, bus = _build_orch(tmp_path, adapter=adapter)
    await orch.start()
    try:
        from mod_tui.events import UserMessageToOrchestrator
        bus.publish(UserMessageToOrchestrator("/help"))
        await orch.wait_idle()
        # Adapter saw the prompt as a query.
        assert adapter._next_query_index == 1
    finally:
        await orch.stop()


@pytest.mark.asyncio
async def test_reset_preserves_old_transcript_creates_new(tmp_path):
    from mod_tui.events import (
        OrchestratorSessionSwitched, UserMessageToOrchestrator,
    )

    adapter1 = _RecordingAdapter(scripts=[_ok_script(session_id="first")])
    adapter2 = _RecordingAdapter(scripts=[_ok_script(session_id="second")])

    bus = EventBus()
    manager = AgentManager(
        cwd=tmp_path, bus=bus,
        adapter_factory=lambda: FakeSDKAdapter(scripts=[]),
    )
    orch = OrchestratorSession(cwd=tmp_path, bus=bus, manager=manager, adapter=adapter1)
    orch._next_adapter_factory = lambda: adapter2

    switched: list[OrchestratorSessionSwitched] = []
    bus.subscribe(OrchestratorSessionSwitched, switched.append)

    await orch.start()
    try:
        # First turn so the transcript file exists.
        bus.publish(UserMessageToOrchestrator("hello"))
        await orch.wait_idle()
        old_path = orch.active_transcript_path
        assert old_path is not None
        assert old_path.exists()

        bus.publish(UserMessageToOrchestrator("/reset"))
        await orch.wait_idle()
        new_path = orch.active_transcript_path

        assert old_path != new_path
        assert old_path.exists(), "old transcript must remain on disk"
        assert len(switched) == 1
        assert switched[0].session_id == orch._sdk_session_id
    finally:
        await orch.stop()


@pytest.mark.asyncio
async def test_concurrent_resets_serialize(tmp_path):
    """Two /reset calls fired back-to-back must both complete cleanly."""
    from mod_tui.events import UserMessageToOrchestrator

    adapter1 = _RecordingAdapter(scripts=[_ok_script(session_id="first")])
    adapter2 = _RecordingAdapter(scripts=[_ok_script(session_id="second")])
    adapter3 = _RecordingAdapter(scripts=[_ok_script(session_id="third")])

    bus = EventBus()
    manager = AgentManager(
        cwd=tmp_path, bus=bus,
        adapter_factory=lambda: FakeSDKAdapter(scripts=[]),
    )
    orch = OrchestratorSession(cwd=tmp_path, bus=bus, manager=manager, adapter=adapter1)
    # Both factories are available for the two consecutive resets.
    factories = iter([lambda: adapter2, lambda: adapter3])

    # Hook so each swap pulls the next factory before delegating.
    original_swap = orch._swap_inner
    async def _swap(*, resume):
        try:
            orch._next_adapter_factory = next(factories)
        except StopIteration:
            pass
        await original_swap(resume=resume)
    orch._swap_inner = _swap

    await orch.start()
    try:
        bus.publish(UserMessageToOrchestrator("/reset"))
        bus.publish(UserMessageToOrchestrator("/reset"))
        await orch.wait_idle()
        # Send a probe so the active adapter streams its ResultMessage, which
        # causes _on_session_id_observed to fire and confirm the third session.
        bus.publish(UserMessageToOrchestrator("probe"))
        await orch.wait_idle()
        # After two resets the active session is the third.
        assert orch._sdk_session_id == "third"
    finally:
        await orch.stop()


@pytest.mark.asyncio
async def test_resume_known_session_passes_resume_to_sdk(tmp_path):
    from mod_tui.events import OrchestratorSessionSwitched

    idx = OrchestratorSessionsIndex(cwd=tmp_path)
    idx.upsert(OrchestratorSessionEntry(
        session_id="target",
        transcript_path=str(tmp_path / ".mod_tui" / "transcripts" / "orchestrator.target.jsonl"),
        started_at=100.0, last_activity=200.0,
    ))

    adapter1 = _RecordingAdapter(scripts=[_ok_script(session_id="boot")])
    adapter2 = _RecordingAdapter(scripts=[_ok_script(session_id="target")])
    bus = EventBus()
    manager = AgentManager(
        cwd=tmp_path, bus=bus,
        adapter_factory=lambda: FakeSDKAdapter(scripts=[]),
    )
    orch = OrchestratorSession(cwd=tmp_path, bus=bus, manager=manager, adapter=adapter1)
    orch._next_adapter_factory = lambda: adapter2
    switched: list[OrchestratorSessionSwitched] = []
    bus.subscribe(OrchestratorSessionSwitched, switched.append)

    await orch.start()
    try:
        await orch.resume("target")
        assert adapter2.last_options.resume == "target"
        assert switched[-1].session_id == "target"
    finally:
        await orch.stop()


@pytest.mark.asyncio
async def test_resume_unknown_session_is_noop_with_notice(tmp_path):
    from mod_tui.events import OrchestratorReply

    adapter = _RecordingAdapter(scripts=[_ok_script()])
    orch, bus = _build_orch(tmp_path, adapter=adapter)
    replies: list[OrchestratorReply] = []
    bus.subscribe(OrchestratorReply, replies.append)

    await orch.start()
    try:
        before = orch._sdk_session_id
        await orch.resume("does-not-exist")
        assert orch._sdk_session_id == before  # no swap
        assert any("no such session" in r.text.lower() for r in replies)
    finally:
        await orch.stop()


@pytest.mark.asyncio
async def test_resume_legacy_falls_back_to_reset(tmp_path):
    from mod_tui.events import OrchestratorReply

    idx = OrchestratorSessionsIndex(cwd=tmp_path)
    idx.upsert(OrchestratorSessionEntry(
        session_id="legacy-99", transcript_path="x.jsonl",
        started_at=100.0, last_activity=200.0, legacy=True,
    ))
    adapter1 = _RecordingAdapter(scripts=[_ok_script(session_id="boot")])
    adapter2 = _RecordingAdapter(scripts=[_ok_script(session_id="fresh")])
    bus = EventBus()
    manager = AgentManager(
        cwd=tmp_path, bus=bus,
        adapter_factory=lambda: FakeSDKAdapter(scripts=[]),
    )
    orch = OrchestratorSession(cwd=tmp_path, bus=bus, manager=manager, adapter=adapter1)
    orch._next_adapter_factory = lambda: adapter2
    replies: list[OrchestratorReply] = []
    bus.subscribe(OrchestratorReply, replies.append)

    await orch.start()
    try:
        await orch.resume("legacy-99")
        assert adapter2.last_options.resume is None  # fresh, not resumed
        assert any("predates" in r.text.lower() or "fresh" in r.text.lower()
                   for r in replies)
    finally:
        await orch.stop()


class _RejectingAdapter(_RecordingAdapter):
    def __init__(self, scripts, reject_resume_id: str):
        super().__init__(scripts)
        self._reject_id = reject_resume_id

    async def start(self, *, options):
        if options.resume == self._reject_id:
            raise RuntimeError("simulated SDK rejection")
        await super().start(options=options)


@pytest.mark.asyncio
async def test_orchestrator_chat_uses_active_transcript_path(tmp_path):
    """Smoke: the chat panel renders with the per-session transcript path."""
    from textual.app import App

    from mod_tui.widgets.orchestrator_chat import OrchestratorChat
    from mod_tui.widgets.rich_transcript import RichTranscript
    from mod_tui.persistence.transcript_store import (
        AgentTranscript, TranscriptEntry,
    )

    # Pre-seed an index entry + transcript file so start() resumes.
    idx = OrchestratorSessionsIndex(cwd=tmp_path)
    sid = "preseeded"
    transcript_path = tmp_path / ".mod_tui" / "transcripts" / f"orchestrator.{sid}.jsonl"
    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    AgentTranscript(cwd=tmp_path, agent_id="orchestrator", path=transcript_path).append(
        TranscriptEntry(role="user", text="from-preseed"),
    )
    idx.upsert(OrchestratorSessionEntry(
        session_id=sid, transcript_path=str(transcript_path),
        started_at=100.0, last_activity=200.0,
    ))

    adapter = _RecordingAdapter(scripts=[_ok_script(session_id=sid)])
    orch, bus = _build_orch(tmp_path, adapter=adapter)
    await orch.start()

    class _Host(App):
        def __init__(self, _orch):
            super().__init__()
            self.event_bus = bus
            self.orchestrator = _orch
            self.cwd = tmp_path

        def compose(self):
            yield OrchestratorChat(event_bus=self.event_bus)

    host = _Host(orch)
    try:
        async with host.run_test() as pilot:
            await pilot.pause()
            rich = host.query_one(RichTranscript)
            assert "from-preseed" in rich.rendered_text()
    finally:
        await orch.stop()


@pytest.mark.asyncio
async def test_resume_falls_back_when_sdk_rejects(tmp_path):
    from mod_tui.events import OrchestratorReply

    idx = OrchestratorSessionsIndex(cwd=tmp_path)
    idx.upsert(OrchestratorSessionEntry(
        session_id="bad", transcript_path="x.jsonl",
        started_at=100.0, last_activity=200.0,
    ))
    boot_adapter = _RecordingAdapter(scripts=[_ok_script(session_id="boot")])
    rejecting = _RejectingAdapter(scripts=[_ok_script(session_id="bad")], reject_resume_id="bad")
    fresh_adapter = _RecordingAdapter(scripts=[_ok_script(session_id="fresh")])

    bus = EventBus()
    manager = AgentManager(
        cwd=tmp_path, bus=bus,
        adapter_factory=lambda: FakeSDKAdapter(scripts=[]),
    )
    orch = OrchestratorSession(cwd=tmp_path, bus=bus, manager=manager, adapter=boot_adapter)
    factories = iter([lambda: rejecting, lambda: fresh_adapter])
    original_swap = orch._swap_inner
    async def _swap(*, resume):
        try:
            orch._next_adapter_factory = next(factories)
        except StopIteration:
            pass
        await original_swap(resume=resume)
    orch._swap_inner = _swap

    replies: list[OrchestratorReply] = []
    bus.subscribe(OrchestratorReply, replies.append)

    await orch.start()
    try:
        await orch.resume("bad")
        # Send a probe to flush the ResultMessage from the fresh adapter.
        from mod_tui.events import UserMessageToOrchestrator
        bus.publish(UserMessageToOrchestrator("probe"))
        await orch.wait_idle()
        # Index entry for "bad" preserved.
        assert idx.get("bad") is not None
        # Active session is the fresh fallback.
        assert orch._sdk_session_id == "fresh"
        assert any("could not resume" in r.text.lower() for r in replies)
    finally:
        await orch.stop()
