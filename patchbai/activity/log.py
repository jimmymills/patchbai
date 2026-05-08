from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Iterable


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


from patchbai.events import (  # noqa: E402 — deferred to avoid circular import
    ActivityLogged, AgentArchiveChanged, AgentMessageAppended,
    AgentNotifiedOrchestrator, AgentRequestedUserInput, AgentSpawned,
    AgentStateChanged, EventBus,
)


class ActivityLog:
    """App-singleton capture of curated EventBus events. Stores the last 500
    normalized entries in a deque. Publishes ActivityLogged after every
    append so subscribers can react incrementally without re-walking the
    backlog. Mode filtering is the consumer's responsibility — the log
    captures the union."""

    BUFFER_SIZE = 500

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self._entries: deque[ActivityEntry] = deque(maxlen=self.BUFFER_SIZE)
        self._wire_agent_subs(bus)

    def entries(self) -> tuple[ActivityEntry, ...]:
        """Snapshot of current entries, oldest first."""
        return tuple(self._entries)

    # --- agent subs --------------------------------------------------------

    def _wire_agent_subs(self, bus: EventBus) -> None:
        bus.subscribe(AgentSpawned, self._on_agent_spawned)
        bus.subscribe(AgentStateChanged, self._on_agent_state)
        bus.subscribe(AgentMessageAppended, self._on_agent_message)
        bus.subscribe(AgentRequestedUserInput, self._on_agent_ask)
        bus.subscribe(AgentNotifiedOrchestrator, self._on_agent_notify)
        bus.subscribe(AgentArchiveChanged, self._on_agent_archive)

    def _on_agent_spawned(self, event: AgentSpawned) -> None:
        info = event.info
        self._append(
            kind=ActivityKind.AGENT_SPAWNED,
            summary=f"{info.name} spawned",
            detail=f"cwd: {info.cwd}",
            agent_id=info.id,
            tab_id=None,
            raw=event,
        )

    def _on_agent_state(self, event: AgentStateChanged) -> None:
        info = event.info
        kind = ActivityKind.AGENT_DONE if info.state.is_terminal else ActivityKind.AGENT_STATE
        summary = f"{info.name}: {event.old_state.value} → {info.state.value}"
        self._append(
            kind=kind, summary=summary, detail=None,
            agent_id=info.id, tab_id=None, raw=event,
        )

    def _on_agent_message(self, event: AgentMessageAppended) -> None:
        if event.role in ("user", "assistant"):
            kind = ActivityKind.AGENT_MESSAGE
            detail = event.text
        elif event.role in ("tool_use", "tool_result"):
            kind = ActivityKind.AGENT_TOOL
            detail = event.tool_name or event.text
        else:
            return  # thinking/system are not surfaced in the feed
        self._append(
            kind=kind,
            summary=event.agent_id,
            detail=detail,
            agent_id=event.agent_id,
            tab_id=None,
            raw=event,
        )

    def _on_agent_ask(self, event: AgentRequestedUserInput) -> None:
        self._append(
            kind=ActivityKind.AGENT_ASK,
            summary=event.agent_id,
            detail=event.question,
            agent_id=event.agent_id,
            tab_id=None,
            raw=event,
        )

    def _on_agent_notify(self, event: AgentNotifiedOrchestrator) -> None:
        self._append(
            kind=ActivityKind.AGENT_NOTIFY,
            summary=event.agent_id,
            detail=event.message,
            agent_id=event.agent_id,
            tab_id=None,
            raw=event,
        )

    def _on_agent_archive(self, event: AgentArchiveChanged) -> None:
        info = event.info
        self._append(
            kind=ActivityKind.AGENT_ARCHIVE,
            summary=f"{info.name} {'archived' if info.archived else 'unarchived'}",
            detail=None,
            agent_id=info.id,
            tab_id=None,
            raw=event,
        )

    # --- append ------------------------------------------------------------

    def _append(
        self, *, kind: str, summary: str, detail: str | None,
        agent_id: str | None, tab_id: str | None, raw: object,
    ) -> None:
        entry = ActivityEntry(
            timestamp=datetime.now(),
            kind=kind, summary=summary, detail=detail,
            agent_id=agent_id, tab_id=tab_id, raw=raw,
        )
        self._entries.append(entry)
        self._bus.publish(ActivityLogged(entry=entry))
