from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, TypeVar

from patchbai.agents.state import AgentInfo, AgentState

if TYPE_CHECKING:
    from patchbai.layout.spec import LayoutSpec

log = logging.getLogger(__name__)

E = TypeVar("E")
Handler = Callable[[E], None]
Unsubscribe = Callable[[], None]


# --- Built-in event types (more added by later plans) ----------------------

@dataclass(frozen=True)
class UserMessageToOrchestrator:
    """User typed something into the orchestrator chat or command bar."""
    text: str


@dataclass(frozen=True)
class OrchestratorReply:
    """The orchestrator session emitted a reply."""
    text: str


@dataclass(frozen=True)
class OrchestratorSessionSwitched:
    """The orchestrator session was swapped (via /reset or /resume)."""
    session_id: str
    transcript_path: str


@dataclass(frozen=True)
class OpenResumePicker:
    """Request from the orchestrator that the app open the resume modal."""
    pass


@dataclass(frozen=True)
class StatsUpdated:
    """StatusBar stats refresh."""
    tokens_in: int = 0
    tokens_out: int = 0
    cost: float = 0.0
    active_agents: int = 0


# --- Agent event types -----------------------------------------------------

@dataclass(frozen=True)
class AgentSpawned:
    """A new child agent has been created and registered with AgentManager."""
    info: AgentInfo


@dataclass(frozen=True)
class AgentStateChanged:
    """An agent transitioned between states (e.g., RUNNING → DONE)."""
    info: AgentInfo
    old_state: AgentState


@dataclass(frozen=True)
class AgentTokensTouched:
    """An AgentSession (orchestrator inner session or child agent) just
    accumulated tokens / cost from a ResultMessage. Lightweight signal — no
    deltas, no totals; subscribers re-aggregate from the canonical AgentInfo
    objects."""
    agent_id: str


@dataclass(frozen=True)
class AgentMessageAppended:
    """A new message landed in an agent's transcript."""
    agent_id: str
    role: str  # "user" | "assistant" | "tool_use" | "tool_result" | "thinking" | "system"
    text: str
    tool_id: str | None = None       # set for role in {"tool_use", "tool_result"}
    tool_name: str | None = None     # set for role == "tool_use"


# Forward-declared for plan 3; the handler arrives later.
@dataclass(frozen=True)
class AgentRequestedUserInput:
    """A child agent called ask_orchestrator and is blocked waiting on a reply."""
    agent_id: str
    question: str
    request_id: str


@dataclass(frozen=True)
class AgentNotifiedOrchestrator:
    """A child agent called notify_orchestrator (fire-and-forget)."""
    agent_id: str
    message: str


@dataclass(frozen=True)
class AgentArchiveChanged:
    """An agent's `archived` flag was toggled. Carries a frozen snapshot of
    the AgentInfo so subscribers (AgentTable, persistence) can refresh."""
    info: AgentInfo


@dataclass(frozen=True)
class DirectMessageToAgent:
    """User typed directly to a focused AgentTranscript's input."""
    agent_id: str
    text: str


# --- Layout event types ----------------------------------------------------

@dataclass(frozen=True)
class LayoutApplied:
    """The LayoutEngine successfully applied a new spec."""
    spec: "LayoutSpec"
    layout_name: str | None = None  # if loaded by name; else None
    tab_id: str | None = None  # set when published per-tab


@dataclass(frozen=True)
class LayoutFailed:
    """The LayoutEngine rejected a spec at build time."""
    error: str
    tab_id: str | None = None


@dataclass(frozen=True)
class LayoutResized:
    """User finished a mouse-drag resize on a Splitter between two siblings.

    Carries the parent container's post-drag layout state as a tuple of
    `outer_size` cell counts — one entry per *non-splitter* child, in spec
    order. The app handler renormalizes those cells into percentages that
    sum to 100%, which keeps the layout converged across re-saves instead of
    drifting (the previous design rounded each pair independently using
    inner widths and was off by border + splitter cells per drag)."""
    tab_id: str
    parent_path: tuple[int, ...]
    children_cells: tuple[int, ...]


@dataclass(frozen=True)
class TabAdded:
    tab_id: str
    title: str


@dataclass(frozen=True)
class TabClosed:
    tab_id: str


@dataclass(frozen=True)
class TabSwitched:
    tab_id: str
    title: str


@dataclass(frozen=True)
class WorkspaceCwdChanged:
    """The app's working directory has been re-rooted at runtime. The
    workspace state has already been reloaded from `cwd` and the active
    layout re-applied; subscribers should re-render any cwd-dependent UI."""
    cwd: str


@dataclass(frozen=True)
class FileSelected:
    """A FileTree (or similar) widget selected a file. Other widgets like
    FileViewer can subscribe with `follow_selection=True` to react."""
    path: str


@dataclass(frozen=True)
class ActivityLogged:
    """A new entry was appended to the app's ActivityLog. Subscribers (e.g.,
    ActivityFeed widgets) consume this to render the new entry. The `entry`
    field is an `ActivityEntry` from `patchbai.activity.log`; we leave it
    typed as `object` here to avoid a circular import."""
    entry: object


@dataclass(frozen=True)
class AgentFocusRequested:
    """An ActivityFeed row click (or other UI affordance) wants to focus a
    specific agent. AgentTable subscribes and selects + scrolls to the
    matching row; if no AgentTable is mounted the click handler falls back
    to opening the agent's TranscriptScreen."""
    agent_id: str


# --- The bus ---------------------------------------------------------------

class EventBus:
    """Synchronous in-process pub/sub keyed by event class.

    Handlers are called in subscription order. Handler exceptions are logged
    and swallowed so one bad handler can't take down the others.
    """

    def __init__(self) -> None:
        self._subs: dict[type, list[Callable]] = {}

    def subscribe(self, event_type: type[E], handler: Callable[[E], None]) -> Unsubscribe:
        self._subs.setdefault(event_type, []).append(handler)

        def unsubscribe() -> None:
            handlers = self._subs.get(event_type)
            if handlers and handler in handlers:
                handlers.remove(handler)

        return unsubscribe

    def publish(self, event: object) -> None:
        """Dispatch `event` to all current subscribers of `type(event)`.

        Handlers are called on a snapshot of the subscriber list, so handlers
        that unsubscribe during dispatch do not interfere with the current
        publish — their removal takes effect on subsequent publishes.

        Subscribers of subclasses are NOT matched (exact-type dispatch only).
        """
        for handler in list(self._subs.get(type(event), [])):
            try:
                handler(event)
            except Exception:
                log.exception("EventBus handler raised")
