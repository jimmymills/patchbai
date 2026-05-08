from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


class ActivityKind:
    """String constants for entry kinds. Plain class instead of an Enum so
    `entry.kind == ActivityKind.AGENT_SPAWNED` and `entry.kind == "agent.spawned"`
    are both valid — keeps test tables and the mode-filter dict ergonomic."""

    AGENT_SPAWNED = "agent.spawned"
    AGENT_STATE = "agent.state"
    AGENT_DONE = "agent.done"
    AGENT_MESSAGE = "agent.message"
    AGENT_TOOL = "agent.tool"
    AGENT_ASK = "agent.ask"
    AGENT_NOTIFY = "agent.notify"
    AGENT_ARCHIVE = "agent.archive"
    ORCH_USER = "orch.user"
    ORCH_REPLY = "orch.reply"
    ORCH_SESSION = "orch.session"
    LAYOUT_APPLIED = "layout.applied"
    LAYOUT_FAILED = "layout.failed"
    TAB_ADDED = "tab.added"
    TAB_CLOSED = "tab.closed"
    TAB_SWITCHED = "tab.switched"
    WORKSPACE_CWD = "workspace.cwd"
    FILE_SELECTED = "file.selected"


@dataclass(frozen=True)
class ActivityEntry:
    """One normalized record in the ActivityLog. `kind` is one of the
    ActivityKind dotted-string constants; `raw` is the original event object
    for debugging/forensics."""
    timestamp: datetime
    kind: str
    summary: str
    detail: str | None
    agent_id: str | None
    tab_id: str | None
    raw: object
