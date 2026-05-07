import asyncio
import uuid
from typing import Callable


class RequestInbox:
    """Per-agent registry of pending ask_orchestrator requests.

    Each registered request_id has an asyncio.Future. The agent's tool call
    awaits the future (with a timeout); the orchestrator's reply resolves it.

    `on_pending_changed`, if provided, is invoked synchronously after every
    transition that changes the pending count — i.e., after `register()` and
    after `wait()` removes a future from the dict. It receives the new
    pending count.
    """

    def __init__(
        self,
        *,
        on_pending_changed: Callable[[int], None] | None = None,
    ) -> None:
        self._futures: dict[str, asyncio.Future] = {}
        self._on_pending_changed = on_pending_changed

    def register(self) -> str:
        request_id = uuid.uuid4().hex[:12]
        # get_running_loop() instead of get_event_loop(): the latter is
        # deprecated in Python 3.12+ when called outside a running loop.
        # All callers run inside an event loop (tool handlers are async).
        loop = asyncio.get_running_loop()
        self._futures[request_id] = loop.create_future()
        self._notify()
        return request_id

    def resolve(self, request_id: str, response: str) -> None:
        future = self._futures.get(request_id)
        if future is not None and not future.done():
            future.set_result(response)

    async def wait(self, request_id: str, *, timeout_s: float) -> str:
        future = self._futures.get(request_id)
        if future is None:
            raise KeyError(f"unknown request_id: {request_id}")
        try:
            return await asyncio.wait_for(future, timeout=timeout_s)
        finally:
            self._futures.pop(request_id, None)
            self._notify()

    def pending(self) -> list[str]:
        return [rid for rid, fut in self._futures.items() if not fut.done()]

    def _notify(self) -> None:
        if self._on_pending_changed is None:
            return
        try:
            self._on_pending_changed(len(self._futures))
        except Exception:
            # The inbox must not poison its own callers if a subscriber
            # explodes; mirror EventBus's swallow-and-log posture.
            import logging
            logging.getLogger(__name__).exception(
                "RequestInbox.on_pending_changed handler raised"
            )
