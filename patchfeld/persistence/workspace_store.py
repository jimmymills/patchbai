import json
import logging
from pathlib import Path

from patchfeld.persistence.atomic import write_json_atomic
from patchfeld.persistence.paths import project_workspace_path
from patchfeld.workspace.spec import Workspace

log = logging.getLogger(__name__)


def save_workspace(cwd: Path, ws: Workspace) -> None:
    write_json_atomic(project_workspace_path(cwd), ws.model_dump(mode="json"))


def load_workspace(cwd: Path) -> Workspace | None:
    path = project_workspace_path(cwd)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return Workspace.model_validate(raw)
    except Exception:
        log.exception("Failed to load workspace from %s", path)
        return None
