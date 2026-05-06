from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, TypeVar

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
