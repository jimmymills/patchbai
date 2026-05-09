from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from patchfeld.persistence.atomic import write_json_atomic
from patchfeld.persistence.paths import project_state_dir

log = logging.getLogger(__name__)


@dataclass
class OrchestratorSessionEntry:
    session_id: str
    transcript_path: str
    started_at: float
    last_activity: float
    first_user_message: str | None = None
    title: str | None = None  # explicit title; defaults to first_user_message-derived
    num_turns: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost: float = 0.0
    legacy: bool = False


def _index_path(cwd: Path) -> Path:
    return project_state_dir(cwd) / "orchestrator_sessions.json"


class OrchestratorSessionsIndex:
    """Per-cwd index of past orchestrator sessions for resume/picker.

    Reads are cached in memory and invalidated by file mtime so the
    orchestrator hot path (which calls `get()` and `upsert()` after
    every assistant turn) does not re-parse the JSON file from disk
    each time. A sibling instance writing the file will be observed
    on the next read because the cached mtime no longer matches disk.
    """

    def __init__(self, cwd: Path) -> None:
        self._cwd = cwd
        self._path = _index_path(cwd)
        self._cache: list[OrchestratorSessionEntry] | None = None
        self._cache_mtime: float | None = None

    def _disk_mtime(self) -> float | None:
        try:
            return self._path.stat().st_mtime
        except FileNotFoundError:
            return None

    def _load_from_disk(self) -> list[OrchestratorSessionEntry]:
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

    def _ensure_cache(self) -> list[OrchestratorSessionEntry]:
        disk_mtime = self._disk_mtime()
        if self._cache is None or disk_mtime != self._cache_mtime:
            self._cache = self._load_from_disk()
            self._cache_mtime = disk_mtime
        return self._cache

    def list(self) -> list[OrchestratorSessionEntry]:
        return list(self._ensure_cache())

    def upsert(self, entry: OrchestratorSessionEntry) -> None:
        cache = self._ensure_cache()
        for i, existing in enumerate(cache):
            if existing.session_id == entry.session_id:
                cache[i] = entry
                self._save(cache)
                return
        cache.append(entry)
        self._save(cache)

    def most_recent(self) -> OrchestratorSessionEntry | None:
        entries = self._ensure_cache()
        if not entries:
            return None
        return max(entries, key=lambda e: e.last_activity)

    def get(self, session_id: str) -> OrchestratorSessionEntry | None:
        for e in self._ensure_cache():
            if e.session_id == session_id:
                return e
        return None

    def set_title(self, session_id: str, title: str | None) -> bool:
        """Update an entry's title. Returns True if the entry was found."""
        entry = self.get(session_id)
        if entry is None:
            return False
        entry.title = title
        self.upsert(entry)
        return True

    def migrate_legacy_if_needed(self) -> None:
        """One-time migration: rename .patchfeld/transcripts/orchestrator.jsonl
        to orchestrator.legacy-<ts>.jsonl and register a legacy=True entry.

        No-op if the index already has any entries OR if no legacy file exists.
        """
        from patchfeld.persistence.paths import project_transcripts_dir

        if self._path.exists():
            return  # index already exists — don't touch

        legacy_path = project_transcripts_dir(self._cwd) / "orchestrator.jsonl"
        if not legacy_path.exists():
            return

        mtime = legacy_path.stat().st_mtime
        legacy_id = f"legacy-{int(mtime)}"
        new_path = project_transcripts_dir(self._cwd) / f"orchestrator.{legacy_id}.jsonl"
        legacy_path.rename(new_path)

        entry = OrchestratorSessionEntry(
            session_id=legacy_id,
            transcript_path=str(new_path.relative_to(self._cwd))
                if new_path.is_relative_to(self._cwd) else str(new_path),
            started_at=mtime,
            last_activity=mtime,
            first_user_message=None,
            num_turns=0,
            tokens_in=0,
            tokens_out=0,
            cost=0.0,
            legacy=True,
        )
        self.upsert(entry)

    def _save(self, entries: list[OrchestratorSessionEntry]) -> None:
        write_json_atomic(self._path, [asdict(e) for e in entries])
        self._cache = entries
        self._cache_mtime = self._disk_mtime()
