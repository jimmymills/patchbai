"""Persistent + session-scoped grant rules for tool permissions.

DESIGN TRADEOFF (revisit when convenient):
This module keys persistent grants by ``(agent_name, tool_name)``. The
agent_name is the literal "orchestrator" for the user's main session and
the user-supplied name for child agents. Respawning a child of the same
name reuses prior decisions — convenient in the common case, surprising
if a name is reused with different intent.

A future revision could swap the disk-backed lookup for an in-memory one
keyed by ``agent_id`` (scoped to one live spawn). The interface here is
intentionally narrow so the swap touches only this file.
"""

import json
import logging
from pathlib import Path
from typing import Literal

from patchbai.persistence.atomic import write_json_atomic
from patchbai.persistence.paths import project_state_dir

log = logging.getLogger(__name__)

Behavior = Literal["allow", "deny"]
Scope = Literal["persistent", "session"]


def _grants_path(cwd: Path) -> Path:
    return project_state_dir(cwd) / "permission_grants.json"


class PermissionGrants:
    """Disk-backed allow/deny rules keyed by (agent_name, tool_name).

    `remember(scope="session")` rules live in-memory only and evaporate on
    process exit. `remember(scope="persistent")` rules are serialized to
    `<cwd>/.patchbai/permission_grants.json`.
    """

    def __init__(self, *, cwd: Path) -> None:
        self._cwd = Path(cwd)
        self._disk: dict[tuple[str, str], Behavior] = {}
        self._session: dict[tuple[str, str], Behavior] = {}
        self._load_disk()

    def lookup(self, *, agent_name: str, tool_name: str) -> Behavior | None:
        # Disk wins over session — disk represents an explicit "always" the
        # user chose earlier, session is "for this run." If both exist, the
        # persistent decision is more authoritative.
        key = (agent_name, tool_name)
        return self._disk.get(key) or self._session.get(key)

    def remember(
        self,
        *,
        agent_name: str,
        tool_name: str,
        behavior: Behavior,
        scope: Scope = "persistent",
    ) -> None:
        key = (agent_name, tool_name)
        if scope == "persistent":
            self._disk[key] = behavior
            self._write_disk()
        else:
            self._session[key] = behavior

    def clear(self) -> None:
        self._disk.clear()
        self._session.clear()
        try:
            _grants_path(self._cwd).unlink()
        except FileNotFoundError:
            pass

    def _load_disk(self) -> None:
        path = _grants_path(self._cwd)
        if not path.exists():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            for entry in raw.get("grants", []):
                key = (entry["agent_name"], entry["tool_name"])
                self._disk[key] = entry["behavior"]
        except (json.JSONDecodeError, OSError, KeyError, TypeError):
            log.exception("permission_grants.json unreadable; starting empty")
            self._disk.clear()

    def _write_disk(self) -> None:
        data = {
            "version": 1,
            "grants": [
                {"agent_name": a, "tool_name": t, "behavior": b}
                for (a, t), b in sorted(self._disk.items())
            ],
        }
        write_json_atomic(_grants_path(self._cwd), data)
