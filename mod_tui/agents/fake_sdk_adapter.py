from typing import AsyncIterator

from claude_agent_sdk import ClaudeAgentOptions


class FakeSDKAdapter:
    """Replays canned message streams. One script per expected query call."""

    def __init__(self, scripts: list[list[object]]) -> None:
        self._scripts = scripts
        self._next_query_index = 0
        self._pending: list[object] = []
        self._started = False

    async def start(self, *, options: ClaudeAgentOptions) -> None:
        self._started = True

    async def query(self, prompt: str) -> None:
        assert self._started, "start() must be called before query()"
        if self._next_query_index >= len(self._scripts):
            raise IndexError(
                f"FakeSDKAdapter has no script for query #{self._next_query_index} "
                f"(only {len(self._scripts)} provided)"
            )
        self._pending = list(self._scripts[self._next_query_index])
        self._next_query_index += 1

    def stream(self) -> AsyncIterator[object]:
        # Snapshot _pending into a local so calling stream() then mutating
        # _pending mid-iteration doesn't cause skipping.
        msgs = self._pending
        self._pending = []

        async def _agen() -> AsyncIterator[object]:
            for m in msgs:
                yield m

        return _agen()

    async def interrupt(self) -> None:
        # No-op for the fake.
        return

    async def stop(self) -> None:
        self._started = False
