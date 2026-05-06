import asyncio
import time
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions

from mod_tui.agents.manager import AgentManager
from mod_tui.agents.sdk_adapter import RealSDKAdapter, SDKAdapter
from mod_tui.agents.session import AgentSession
from mod_tui.agents.state import AgentInfo, AgentState
from mod_tui.events import (
    AgentMessageAppended,
    EventBus,
    OrchestratorReply,
    UserMessageToOrchestrator,
)
from mod_tui.orchestrator.tools import build_orchestrator_mcp_server
from mod_tui.persistence.transcript_store import AgentTranscript


class OrchestratorSession:
    """The user's manager-Claude session. An AgentSession with extra MCP tools."""

    AGENT_ID = "orchestrator"

    def __init__(
        self,
        *,
        cwd: Path,
        bus: EventBus,
        manager: AgentManager,
        adapter: SDKAdapter | None = None,
        model: str | None = None,
    ) -> None:
        self._cwd = cwd
        self._bus = bus
        self._manager = manager
        self._model = model
        self._adapter = adapter or RealSDKAdapter()
        self._info = AgentInfo(
            id=self.AGENT_ID,
            name="orchestrator",
            cwd=str(cwd),
            started_at=time.time(),
        )
        self._inner = AgentSession(
            info=self._info,
            adapter=self._adapter,
            transcript=AgentTranscript(cwd=cwd, agent_id=self.AGENT_ID),
            bus=bus,
        )
        self._unsub_user: callable = lambda: None
        self._unsub_msg: callable = lambda: None
        self._send_tasks: list[asyncio.Task] = []

    async def start(self) -> None:
        mcp_server = build_orchestrator_mcp_server(self._manager)
        options_kwargs: dict = {
            "cwd": str(self._cwd),
            "mcp_servers": {"mod_tui_orchestrator": mcp_server},
            # The orchestrator is the user's trusted manager session — there's
            # no UI in the TUI yet to render a permission prompt, so the SDK
            # would hang waiting for one. Bypass for now; a Textual modal-
            # based can_use_tool callback is plan-3 work.
            "permission_mode": "bypassPermissions",
        }
        if self._model is not None:
            options_kwargs["model"] = self._model
        await self._inner.start(options=ClaudeAgentOptions(**options_kwargs))

        self._unsub_user = self._bus.subscribe(
            UserMessageToOrchestrator, self._on_user_message
        )
        self._unsub_msg = self._bus.subscribe(
            AgentMessageAppended, self._on_message_appended
        )

    async def wait_idle(self) -> None:
        # Drain any UserMessageToOrchestrator-triggered create_tasks that may
        # have been scheduled but not yet started. Two yields is enough to
        # cover the worst case (sync subscribe → create_task → coroutine
        # body's first await).
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        # Wait for every outstanding send task to complete so that all queued
        # messages have been fully processed (including the second+ messages
        # that are serialised behind the AgentSession._send_lock).
        if self._send_tasks:
            pending = [t for t in self._send_tasks if not t.done()]
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            self._send_tasks.clear()
        await self._inner.wait_idle()

    async def stop(self) -> None:
        self._unsub_user()
        self._unsub_msg()
        await self._inner.stop()

    # --- internals --------------------------------------------------------

    def _on_user_message(self, event: UserMessageToOrchestrator) -> None:
        # The bus is sync — schedule the async send on the running loop.
        task = asyncio.create_task(self._inner.send(event.text))
        self._send_tasks.append(task)

    def _on_message_appended(self, event: AgentMessageAppended) -> None:
        if event.agent_id != self.AGENT_ID:
            return
        if event.role != "assistant":
            return
        self._bus.publish(OrchestratorReply(event.text))
