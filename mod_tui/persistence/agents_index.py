import json
import logging
from pathlib import Path

from mod_tui.agents.state import AgentInfo
from mod_tui.persistence.atomic import write_json_atomic
from mod_tui.persistence.paths import project_state_dir

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
