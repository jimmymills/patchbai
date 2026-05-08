import json
import logging
from dataclasses import asdict, dataclass, fields
from pathlib import Path

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

    def append(self, entry: TranscriptEntry) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(entry)) + "\n")

    def read_all(self) -> list[TranscriptEntry]:
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
