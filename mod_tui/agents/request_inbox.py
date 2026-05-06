import asyncio
import uuid


class RequestInbox:
    """Per-agent registry of pending ask_orchestrator requests.

    Each registered request_id has an asyncio.Future. The agent's tool call
    awaits the future (with a timeout); the orchestrator's reply resolves it.
    """

    def __init__(self) -> None:
        self._futures: dict[str, asyncio.Future] = {}

    def register(self) -> str:
        request_id = uuid.uuid4().hex[:12]
        loop = asyncio.get_event_loop()
        self._futures[request_id] = loop.create_future()
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

    def pending(self) -> list[str]:
        return [rid for rid, fut in self._futures.items() if not fut.done()]
