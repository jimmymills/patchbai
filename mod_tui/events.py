from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, TypeVar

from mod_tui.agents.state import AgentInfo, AgentState

if TYPE_CHECKING:
    from mod_tui.layout.spec import LayoutSpec

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
class AgentMessageAppended:
    """A new message landed in an agent's transcript."""
    agent_id: str
    role: str  # "user" | "assistant" | "tool_use" | "tool_result" | "system"
    text: str


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


@dataclass(frozen=True)
class LayoutFailed:
    """The LayoutEngine rejected a spec at build time."""
    error: str


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
