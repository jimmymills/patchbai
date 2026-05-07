import json
import logging
import re
from pathlib import Path

from patchbai.persistence.atomic import write_json_atomic
from patchbai.theme.spec import ThemeSpec

log = logging.getLogger(__name__)

_VALID_NAME = re.compile(r"^[A-Za-z0-9_\-]+$")


class NamedThemesStore:
    """Read/write named ThemeSpecs at <global_dir>/themes/<name>.json."""

    def __init__(self, global_dir: Path) -> None:
        self._dir = Path(global_dir) / "themes"

    def save(self, name: str, spec: ThemeSpec) -> None:
        if not name or not _VALID_NAME.match(name):
            raise ValueError(
                f"theme name must match {_VALID_NAME.pattern!r}, got {name!r}"
            )
        write_json_atomic(self._dir / f"{name}.json", spec.model_dump(mode="json"))

    def load(self, name: str) -> ThemeSpec | None:
        path = self._dir / f"{name}.json"
        if not path.exists():
            return None
        try:
            return ThemeSpec.model_validate(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            log.exception("Failed to load named theme %r", name)
            return None

    def list(self) -> list[str]:
        if not self._dir.exists():
            return []
        names = []
        for p in self._dir.iterdir():
            if p.is_file() and p.suffix == ".json":
                names.append(p.stem)
        return sorted(names)
