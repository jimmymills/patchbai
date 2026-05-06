import json
import logging
from pathlib import Path

from mod_tui.layout.spec import LayoutSpec
from mod_tui.persistence.atomic import write_json_atomic
from mod_tui.persistence.paths import project_layout_path

log = logging.getLogger(__name__)


def save_layout(cwd: Path, spec: LayoutSpec) -> None:
    write_json_atomic(project_layout_path(cwd), spec.model_dump(mode="json"))


def load_layout(cwd: Path) -> LayoutSpec | None:
    path = project_layout_path(cwd)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text())
        return LayoutSpec.model_validate(raw)
    except Exception:
        log.exception("Failed to load layout from %s", path)
        return None
