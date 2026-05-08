from __future__ import annotations

from collections import deque
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


from patchbai.events import (  # noqa: E402 — deferred to avoid circular import
    ActivityLogged, AgentArchiveChanged, AgentMessageAppended,
    AgentNotifiedOrchestrator, AgentRequestedUserInput, AgentSpawned,
    AgentStateChanged, EventBus, FileSelected, LayoutApplied, LayoutFailed,
    OrchestratorReply, OrchestratorSessionSwitched, TabAdded, TabClosed,
    TabSwitched, UserMessageToOrchestrator, WorkspaceCwdChanged,
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
        self._wire_subscriptions(bus)

    def entries(self) -> tuple[ActivityEntry, ...]:
        """Snapshot of current entries, oldest first."""
        return tuple(self._entries)

    # --- subscriptions -----------------------------------------------------

    def _wire_subscriptions(self, bus: EventBus) -> None:
        bus.subscribe(AgentSpawned, self._on_agent_spawned)
        bus.subscribe(AgentStateChanged, self._on_agent_state)
        bus.subscribe(AgentMessageAppended, self._on_agent_message)
        bus.subscribe(AgentRequestedUserInput, self._on_agent_ask)
        bus.subscribe(AgentNotifiedOrchestrator, self._on_agent_notify)
        bus.subscribe(AgentArchiveChanged, self._on_agent_archive)
        bus.subscribe(UserMessageToOrchestrator, self._on_orch_user)
        bus.subscribe(OrchestratorReply, self._on_orch_reply)
        bus.subscribe(OrchestratorSessionSwitched, self._on_orch_session)
        bus.subscribe(LayoutApplied, self._on_layout_applied)
        bus.subscribe(LayoutFailed, self._on_layout_failed)
        bus.subscribe(TabAdded, self._on_tab_added)
        bus.subscribe(TabClosed, self._on_tab_closed)
        bus.subscribe(TabSwitched, self._on_tab_switched)
        bus.subscribe(WorkspaceCwdChanged, self._on_cwd_changed)
        bus.subscribe(FileSelected, self._on_file_selected)

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

    def _on_orch_user(self, event: UserMessageToOrchestrator) -> None:
        self._append(
            kind=ActivityKind.ORCH_USER, summary="user → orchestrator",
            detail=event.text, agent_id=None, tab_id=None, raw=event,
        )

    def _on_orch_reply(self, event: OrchestratorReply) -> None:
        self._append(
            kind=ActivityKind.ORCH_REPLY, summary="orchestrator → user",
            detail=event.text, agent_id=None, tab_id=None, raw=event,
        )

    def _on_orch_session(self, event: OrchestratorSessionSwitched) -> None:
        self._append(
            kind=ActivityKind.ORCH_SESSION,
            summary=f"session → {event.session_id[:8]}",
            detail=event.transcript_path, agent_id=None, tab_id=None, raw=event,
        )

    def _on_layout_applied(self, event: LayoutApplied) -> None:
        self._append(
            kind=ActivityKind.LAYOUT_APPLIED,
            summary=event.layout_name or "(unnamed)",
            detail=None, agent_id=None, tab_id=event.tab_id, raw=event,
        )

    def _on_layout_failed(self, event: LayoutFailed) -> None:
        self._append(
            kind=ActivityKind.LAYOUT_FAILED, summary="layout failed",
            detail=event.error, agent_id=None, tab_id=event.tab_id, raw=event,
        )

    def _on_tab_added(self, event: TabAdded) -> None:
        self._append(
            kind=ActivityKind.TAB_ADDED, summary=event.title, detail=None,
            agent_id=None, tab_id=event.tab_id, raw=event,
        )

    def _on_tab_closed(self, event: TabClosed) -> None:
        self._append(
            kind=ActivityKind.TAB_CLOSED, summary=event.tab_id, detail=None,
            agent_id=None, tab_id=event.tab_id, raw=event,
        )

    def _on_tab_switched(self, event: TabSwitched) -> None:
        self._append(
            kind=ActivityKind.TAB_SWITCHED, summary=event.title, detail=None,
            agent_id=None, tab_id=event.tab_id, raw=event,
        )

    def _on_cwd_changed(self, event: WorkspaceCwdChanged) -> None:
        self._append(
            kind=ActivityKind.WORKSPACE_CWD, summary=event.cwd, detail=None,
            agent_id=None, tab_id=None, raw=event,
        )

    def _on_file_selected(self, event: FileSelected) -> None:
        self._append(
            kind=ActivityKind.FILE_SELECTED, summary=event.path, detail=None,
            agent_id=None, tab_id=None, raw=event,
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
