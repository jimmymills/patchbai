from mod_tui.events import EventBus, OrchestratorReply, UserMessageToOrchestrator
from mod_tui.persistence.transcript_store import (
    OrchestratorTranscript,
    TranscriptEntry,
)


class FakeOrchestratorSession:
    """Stand-in for the real Claude Agent SDK orchestrator (wired in plan 2).

    Echoes user input back as 'I heard: <text>'. Writes both sides to the
    transcript store so we can verify persistence end-to-end before the real
    SDK is involved.
    """

    def __init__(
        self,
        *,
        bus: EventBus,
        transcript: OrchestratorTranscript | None,
    ) -> None:
        self._bus = bus
        self._transcript = transcript
        self._unsub = lambda: None

    def start(self) -> None:
        self._unsub = self._bus.subscribe(UserMessageToOrchestrator, self._handle)

    def stop(self) -> None:
        self._unsub()

    def _handle(self, event: UserMessageToOrchestrator) -> None:
        if self._transcript is not None:
            self._transcript.append(TranscriptEntry(role="user", text=event.text))
        reply = f"I heard: {event.text}"
        self._bus.publish(OrchestratorReply(reply))
        if self._transcript is not None:
            self._transcript.append(TranscriptEntry(role="orch", text=reply))
