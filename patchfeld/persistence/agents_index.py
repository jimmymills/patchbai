import json
import logging
import time
from pathlib import Path

from patchfeld.agents.state import AgentInfo, AgentState
from patchfeld.persistence.atomic import write_json_atomic
from patchfeld.persistence.paths import project_state_dir

log = logging.getLogger(__name__)


def _index_path(cwd: Path) -> Path:
    return project_state_dir(cwd) / "agents.json"


class AgentsIndex:
    def __init__(self, cwd: Path) -> None:
        self._cwd = cwd
        self._path = _index_path(cwd)

    def load(self) -> list[AgentInfo]:
        if not self._path.exists():
            return []
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            if not isinstance(raw, list):
                log.warning("agents.json is not a list at %s", self._path)
                return []
            return [AgentInfo.from_dict(entry) for entry in raw]
        except Exception:
            log.exception("Failed to load agents.json from %s", self._path)
            return []

    def save(self, infos: list[AgentInfo]) -> None:
        write_json_atomic(self._path, [info.to_dict() for info in infos])

    def upsert(self, info: AgentInfo) -> None:
        current = self.load()
        for i, existing in enumerate(current):
            if existing.id == info.id:
                current[i] = info
                self.save(current)
                return
        current.append(info)
        self.save(current)

    def reconcile_orphans(self) -> list[AgentInfo]:
        # On boot the manager has no live sessions yet, so any persisted
        # agent in a non-terminal state is from a previous process that died
        # without marking it done (e.g. crash). Flip it to ERROR so the table
        # doesn't claim those rows are still running.
        # The orchestrator is excluded — it owns its own boot lifecycle and
        # will overwrite the entry on start().
        infos = self.load()
        now = time.time()
        changed = False
        for info in infos:
            if info.id == "orchestrator":
                continue
            if not info.state.is_terminal:
                info.state = AgentState.ERROR
                if info.ended_at is None:
                    info.ended_at = now
                changed = True
        if changed:
            self.save(infos)
        return infos
