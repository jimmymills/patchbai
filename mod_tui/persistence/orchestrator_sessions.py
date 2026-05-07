from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from mod_tui.persistence.atomic import write_json_atomic
from mod_tui.persistence.paths import project_state_dir

log = logging.getLogger(__name__)


@dataclass
class OrchestratorSessionEntry:
    session_id: str
    transcript_path: str
    started_at: float
    last_activity: float
    first_user_message: str | None = None
    num_turns: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost: float = 0.0
    legacy: bool = False


def _index_path(cwd: Path) -> Path:
    return project_state_dir(cwd) / "orchestrator_sessions.json"


class OrchestratorSessionsIndex:
    """Per-cwd index of past orchestrator sessions for resume/picker."""

    def __init__(self, cwd: Path) -> None:
        self._cwd = cwd
        self._path = _index_path(cwd)

    def list(self) -> list[OrchestratorSessionEntry]:
        if not self._path.exists():
            return []
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            if not isinstance(raw, list):
                log.warning("orchestrator_sessions.json is not a list at %s", self._path)
                return []
            valid = {f.name for f in fields(OrchestratorSessionEntry)}
            out: list[OrchestratorSessionEntry] = []
            for item in raw:
                if not isinstance(item, dict):
                    continue
                kwargs = {k: v for k, v in item.items() if k in valid}
                out.append(OrchestratorSessionEntry(**kwargs))
            return out
        except Exception:
            log.exception("Failed to load orchestrator_sessions.json from %s", self._path)
            return []

    def upsert(self, entry: OrchestratorSessionEntry) -> None:
        current = self.list()
        for i, existing in enumerate(current):
            if existing.session_id == entry.session_id:
                current[i] = entry
                self._save(current)
                return
        current.append(entry)
        self._save(current)

    def most_recent(self) -> OrchestratorSessionEntry | None:
        entries = self.list()
        if not entries:
            return None
        return max(entries, key=lambda e: e.last_activity)

    def get(self, session_id: str) -> OrchestratorSessionEntry | None:
        for e in self.list():
            if e.session_id == session_id:
                return e
        return None

    def _save(self, entries: list[OrchestratorSessionEntry]) -> None:
        write_json_atomic(self._path, [asdict(e) for e in entries])
