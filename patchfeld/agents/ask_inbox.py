import asyncio
import logging
import uuid
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PendingAskUserQuestion:
    request_id: str
    tool_id: str | None
    questions: tuple[dict, ...]


class AskUserQuestionInbox:
    """Per-session registry of in-flight `AskUserQuestion` tool calls.

    Sibling to `PermissionInbox` and `RequestInbox`: same register / wait /
    resolve shape, payload is the user's structured answer (a tuple of
    per-question dicts) instead of a `PermissionResult`. The orchestrator's
    `can_use_tool` callback registers here when it sees an `AskUserQuestion`
    tool call, publishes an event the chat widget renders inline, and
    awaits until the widget calls `resolve(...)` with the picked answers.
    """

    def __init__(self) -> None:
        self._records: dict[str, PendingAskUserQuestion] = {}
        self._futures: dict[str, asyncio.Future] = {}

    def register(
        self,
        *,
        tool_id: str | None,
        questions: tuple[dict, ...],
    ) -> str:
        request_id = uuid.uuid4().hex[:12]
        loop = asyncio.get_running_loop()
        self._futures[request_id] = loop.create_future()
        self._records[request_id] = PendingAskUserQuestion(
            request_id=request_id, tool_id=tool_id, questions=questions,
        )
        return request_id

    def resolve(
        self,
        request_id: str,
        answers: tuple[dict, ...],
    ) -> None:
        future = self._futures.get(request_id)
        if future is not None and not future.done():
            future.set_result(answers)

    async def wait(
        self,
        request_id: str,
        *,
        timeout_s: float,
    ) -> tuple[dict, ...]:
        future = self._futures.get(request_id)
        if future is None:
            raise KeyError(f"unknown request_id: {request_id}")
        try:
            return await asyncio.wait_for(future, timeout=timeout_s)
        finally:
            self._futures.pop(request_id, None)
            self._records.pop(request_id, None)

    def cancel_all(self) -> None:
        for fut in self._futures.values():
            if not fut.done():
                fut.cancel()

    def pending(self) -> list[PendingAskUserQuestion]:
        return list(self._records.values())
