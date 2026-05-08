import asyncio
import logging
import uuid
from dataclasses import dataclass
from typing import Callable

from claude_agent_sdk import PermissionResult

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PendingPermission:
    request_id: str
    tool_name: str
    tool_input: dict
    title: str | None = None
    description: str | None = None


class PermissionInbox:
    """Per-session registry of pending can_use_tool callbacks.

    Sibling to RequestInbox: same register/wait/resolve shape, different
    payload (PermissionResult vs str) and different blocker semantics — the
    AgentSession flips into AWAITING_PERMISSION while count > 0.

    `on_pending_changed`, if provided, is called synchronously after every
    transition that changes the pending count.
    """

    def __init__(
        self,
        *,
        on_pending_changed: Callable[[int], None] | None = None,
    ) -> None:
        self._records: dict[str, PendingPermission] = {}
        self._futures: dict[str, asyncio.Future] = {}
        self._on_pending_changed = on_pending_changed

    def register(
        self,
        *,
        tool_name: str,
        tool_input: dict,
        title: str | None = None,
        description: str | None = None,
    ) -> str:
        request_id = uuid.uuid4().hex[:12]
        loop = asyncio.get_running_loop()
        self._futures[request_id] = loop.create_future()
        self._records[request_id] = PendingPermission(
            request_id=request_id, tool_name=tool_name, tool_input=tool_input,
            title=title, description=description,
        )
        self._notify()
        return request_id

    def resolve(self, request_id: str, result: PermissionResult) -> None:
        future = self._futures.get(request_id)
        if future is not None and not future.done():
            future.set_result(result)

    async def wait(self, request_id: str, *, timeout_s: float) -> PermissionResult:
        future = self._futures.get(request_id)
        if future is None:
            raise KeyError(f"unknown request_id: {request_id}")
        try:
            return await asyncio.wait_for(future, timeout=timeout_s)
        finally:
            self._futures.pop(request_id, None)
            self._records.pop(request_id, None)
            self._notify()

    def cancel_all(self) -> None:
        """Cancel every pending future. Used by AgentManager.kill /
        OrchestratorSession.stop."""
        for fut in self._futures.values():
            if not fut.done():
                fut.cancel()

    def pending(self) -> list[PendingPermission]:
        return list(self._records.values())

    def _notify(self) -> None:
        if self._on_pending_changed is None:
            return
        try:
            self._on_pending_changed(len(self._futures))
        except Exception:
            log.exception("PermissionInbox.on_pending_changed handler raised")
