import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path

from mod_tui.persistence.paths import (
    project_orchestrator_transcript,
    project_transcripts_dir,
)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class TranscriptEntry:
    role: str  # "user" | "orch"
    text: str


class OrchestratorTranscript:
    def __init__(self, cwd: Path) -> None:
        self._path = project_orchestrator_transcript(cwd)
        self._cwd = cwd

    def append(self, entry: TranscriptEntry) -> None:
        project_transcripts_dir(self._cwd).mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(entry)) + "\n")

    def read_all(self) -> list[TranscriptEntry]:
        if not self._path.exists():
            return []
        out: list[TranscriptEntry] = []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                out.append(TranscriptEntry(**json.loads(line)))
            except Exception:
                log.warning("Skipping corrupted transcript line: %r", line)
        return out
