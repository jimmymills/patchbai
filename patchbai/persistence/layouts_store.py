import json
import logging
import re
from pathlib import Path

from patchbai.layout.spec import LayoutSpec
from patchbai.persistence.atomic import write_json_atomic

log = logging.getLogger(__name__)

_VALID_NAME = re.compile(r"^[A-Za-z0-9_\-]+$")


class NamedLayoutsStore:
    """Read/write named LayoutSpecs at <global_dir>/layouts/<name>.json."""

    def __init__(self, global_dir: Path) -> None:
        self._dir = Path(global_dir) / "layouts"

    def save(self, name: str, spec: LayoutSpec) -> None:
        if not name or not _VALID_NAME.match(name):
            raise ValueError(
                f"layout name must match {_VALID_NAME.pattern!r}, got {name!r}"
            )
        write_json_atomic(self._dir / f"{name}.json", spec.model_dump(mode="json"))

    def load(self, name: str) -> LayoutSpec | None:
        path = self._dir / f"{name}.json"
        if not path.exists():
            return None
        try:
            return LayoutSpec.model_validate(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            log.exception("Failed to load named layout %r", name)
            return None

    def list(self) -> list[str]:
        if not self._dir.exists():
            return []
        names = []
        for p in self._dir.iterdir():
            if p.is_file() and p.suffix == ".json":
                names.append(p.stem)
        return sorted(names)
