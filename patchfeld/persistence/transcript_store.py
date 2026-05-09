import json
import logging
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import IO

from patchfeld.persistence.paths import project_transcript_path

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class TranscriptEntry:
    role: str  # "user" | "assistant" | "tool_use" | "tool_result" | "thinking" | "system" | "orch"
    text: str
    tool_id: str | None = None
    tool_name: str | None = None


class AgentTranscript:
    """Append-only JSONL transcript for one agent.

    Holds a single line-buffered append handle for the lifetime of the
    transcript so streaming agents (which emit many small chunks per
    turn) don't pay open+write+close per entry. Line buffering keeps
    each entry durable on disk at \\n exactly like the previous
    open-per-call behavior.

    Call `close()` from the agent stop path to release the file
    descriptor. The handle is also closed on garbage collection as a
    safety net.

    Use agent_id="orchestrator" for the orchestrator's own transcript;
    `OrchestratorTranscript` is provided as a thin alias for that case
    so plan-1 callers don't have to change.
    """

    def __init__(
        self,
        cwd: Path,
        agent_id: str,
        *,
        path: Path | None = None,
    ) -> None:
        self._cwd = cwd
        self._agent_id = agent_id
        self._path = Path(path) if path is not None else project_transcript_path(cwd, agent_id)
        self._handle: IO[str] | None = None
        self._parent_made = False

    def _ensure_handle(self) -> IO[str]:
        if self._handle is not None and not self._handle.closed:
            return self._handle
        if not self._parent_made:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._parent_made = True
        # buffering=1 → line buffered: \n triggers flush to OS, matching
        # the durability of the prior open-per-call code.
        self._handle = self._path.open("a", encoding="utf-8", buffering=1)
        return self._handle

    def append(self, entry: TranscriptEntry) -> None:
        f = self._ensure_handle()
        f.write(json.dumps(asdict(entry)) + "\n")

    def close(self) -> None:
        h = self._handle
        self._handle = None
        if h is not None and not h.closed:
            try:
                h.flush()
            finally:
                h.close()

    def __del__(self) -> None:  # safety net for callers that forget close()
        try:
            self.close()
        except Exception:
            pass

    def read_all(self) -> list[TranscriptEntry]:
        if self._handle is not None and not self._handle.closed:
            self._handle.flush()
        if not self._path.exists():
            return []
        out: list[TranscriptEntry] = []
        valid_keys = {f.name for f in fields(TranscriptEntry)}
        for line in self._path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
                kwargs = {k: v for k, v in raw.items() if k in valid_keys}
                out.append(TranscriptEntry(**kwargs))
            except Exception:
                log.warning("Skipping corrupted transcript line: %r", line)
        return out


class OrchestratorTranscript(AgentTranscript):
    """Plan-1 alias for AgentTranscript(agent_id='orchestrator')."""

    def __init__(self, cwd: Path) -> None:
        super().__init__(cwd=cwd, agent_id="orchestrator")
