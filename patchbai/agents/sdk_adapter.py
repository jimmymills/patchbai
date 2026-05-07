from typing import AsyncIterator, Protocol

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient


class SDKAdapter(Protocol):
    """Thin wrapping of one Claude Agent SDK session.

    The interface is the surface our AgentSession uses — one query at a
    time, async stream of messages until the SDK signals completion. The
    real implementation wraps ClaudeSDKClient; tests use FakeSDKAdapter.
    """

    async def start(self, *, options: ClaudeAgentOptions) -> None: ...
    async def query(self, prompt: str) -> None: ...
    def stream(self) -> AsyncIterator[object]:
        """Yield messages emitted in response to the most recent query.
        Iteration ends when the SDK emits ResultMessage."""
        ...
    async def interrupt(self) -> None: ...
    async def stop(self) -> None: ...


class RealSDKAdapter:
    """Wraps a real ClaudeSDKClient instance."""

    def __init__(self) -> None:
        self._client: ClaudeSDKClient | None = None

    async def start(self, *, options: ClaudeAgentOptions) -> None:
        self._client = ClaudeSDKClient(options=options)
        await self._client.__aenter__()

    async def query(self, prompt: str) -> None:
        assert self._client is not None, "start() must be called before query()"
        await self._client.query(prompt)

    def stream(self) -> AsyncIterator[object]:
        assert self._client is not None, "start() must be called before stream()"
        return self._client.receive_response()

    async def interrupt(self) -> None:
        if self._client is not None:
            await self._client.interrupt()

    async def stop(self) -> None:
        if self._client is not None:
            await self._client.__aexit__(None, None, None)
            self._client = None
