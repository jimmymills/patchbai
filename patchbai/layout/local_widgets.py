from __future__ import annotations

import importlib.util
import logging
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from textual.widget import Widget

from patchbai.layout.registry import WidgetRegistry

log = logging.getLogger(__name__)


LoadStatus = Literal[
    "ok",
    "import_error",
    "no_widget_class",
    "ambiguous_class",
    "name_collision",
]


@dataclass(frozen=True)
class LoadOutcome:
    path: Path
    name: str
    status: LoadStatus
    error: str | None = None


class LocalWidgetLoader:
    """Walks a directory, imports every top-level .py file as an isolated
    module, finds its Textual Widget subclass, and registers it.

    Files starting with '_' or '.' are skipped.
    Missing/empty directory yields an empty outcome list (not an error).
    """

    def __init__(self, dir_path: Path, registry: WidgetRegistry) -> None:
        self._dir = Path(dir_path)
        self._registry = registry

    def load(self) -> list[LoadOutcome]:
        if not self._dir.exists():
            return []
        outcomes: list[LoadOutcome] = []
        for path in sorted(self._dir.iterdir()):
            if not path.is_file() or path.suffix != ".py":
                continue
            if path.name.startswith(("_", ".")):
                continue
            outcomes.append(self._load_one(path))
        return outcomes

    def _load_one(self, path: Path) -> LoadOutcome:
        stem = path.stem
        # Use a unique module name to avoid collisions in sys.modules.
        mod_name = f"_patchbai_local_widget_{stem}"
        try:
            spec = importlib.util.spec_from_file_location(mod_name, path)
            assert spec is not None and spec.loader is not None
            module = importlib.util.module_from_spec(spec)
            sys.modules[mod_name] = module
            spec.loader.exec_module(module)
        except Exception:
            tb = traceback.format_exc(limit=2)
            return LoadOutcome(
                path=path, name=stem, status="import_error", error=tb,
            )

        meta = getattr(module, "__patchbai_widget__", {}) or {}
        if not isinstance(meta, dict):
            meta = {}
        name = meta.get("name") or _pascal(stem)

        # Name-collision with an already-registered builtin: skip.
        existing = self._registry._infos.get(name)
        if existing is not None and existing.source == "builtin":
            return LoadOutcome(
                path=path, name=name, status="name_collision",
                error=f"name {name!r} is reserved by the built-in registry",
            )

        cls, err = _find_widget_class_in_module(module, meta, name)
        if cls is None:
            return LoadOutcome(path=path, name=name, status=err or "no_widget_class")

        self._registry.register(
            name, cls,
            description=meta.get("description", ""),
            props_schema=dict(meta.get("props_schema") or {}),
            source="local",
        )
        return LoadOutcome(path=path, name=name, status="ok")


def _pascal(stem: str) -> str:
    return "".join(part.capitalize() for part in stem.replace("-", "_").split("_") if part)


def _find_widget_class_in_module(module, meta: dict, name: str):
    """Returns (cls_or_none, status_on_failure)."""
    # 1. Explicit entry_point in metadata.
    ep = meta.get("entry_point")
    if isinstance(ep, type) and issubclass(ep, Widget):
        return ep, None

    # 2. WIDGET_CLASS sentinel.
    sentinel = getattr(module, "WIDGET_CLASS", None)
    if isinstance(sentinel, type) and issubclass(sentinel, Widget):
        return sentinel, None

    # 3. Class named exactly `name`.
    by_name = getattr(module, name, None)
    if isinstance(by_name, type) and issubclass(by_name, Widget):
        return by_name, None

    # 4. Single Widget subclass DEFINED in this module.
    candidates = []
    for v in vars(module).values():
        if (
            isinstance(v, type)
            and issubclass(v, Widget)
            and v is not Widget
            and getattr(v, "__module__", None) == module.__name__
        ):
            candidates.append(v)
    unique = list({id(c): c for c in candidates}.values())
    if len(unique) == 1:
        return unique[0], None
    if len(unique) > 1:
        return None, "ambiguous_class"
    return None, "no_widget_class"
