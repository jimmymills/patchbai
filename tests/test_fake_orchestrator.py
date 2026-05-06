from pathlib import Path

from mod_tui.events import EventBus, OrchestratorReply, UserMessageToOrchestrator
from mod_tui.orchestrator.fake_session import FakeOrchestratorSession
from mod_tui.persistence.transcript_store import (
    OrchestratorTranscript,
    TranscriptEntry,
)


def test_fake_session_echoes_user_input():
    bus = EventBus()
    received: list[OrchestratorReply] = []
    bus.subscribe(OrchestratorReply, received.append)

    session = FakeOrchestratorSession(bus=bus, transcript=None)
    session.start()

    bus.publish(UserMessageToOrchestrator("hello"))

    assert received == [OrchestratorReply("I heard: hello")]


def test_fake_session_writes_to_transcript(tmp_path: Path):
    bus = EventBus()
    transcript = OrchestratorTranscript(cwd=tmp_path)

    session = FakeOrchestratorSession(bus=bus, transcript=transcript)
    session.start()

    bus.publish(UserMessageToOrchestrator("ping"))

    assert transcript.read_all() == [
        TranscriptEntry(role="user", text="ping"),
        TranscriptEntry(role="orch", text="I heard: ping"),
    ]


def test_stop_unsubscribes():
    bus = EventBus()
    received: list[OrchestratorReply] = []
    bus.subscribe(OrchestratorReply, received.append)

    session = FakeOrchestratorSession(bus=bus, transcript=None)
    session.start()
    session.stop()

    bus.publish(UserMessageToOrchestrator("hi"))

    assert received == []
