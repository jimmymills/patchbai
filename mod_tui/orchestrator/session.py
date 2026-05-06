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
    AgentNotifiedOrchestrator,
    AgentRequestedUserInput,
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
        apply_layout=None,
        layouts_store=None,
        config_store=None,
        actions=None,
        rebind_keys=None,
        widget_registry=None,
        current_layout=None,
    ) -> None:
        self._cwd = cwd
        self._bus = bus
        self._manager = manager
        self._model = model
        self._adapter = adapter or RealSDKAdapter()
        self._apply_layout = apply_layout
        self._layouts_store = layouts_store
        self._config_store = config_store
        self._actions = actions
        self._rebind_keys = rebind_keys
        self._widget_registry = widget_registry
        self._current_layout = current_layout
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
        self._unsub_notify: callable = lambda: None
        self._unsub_ask: callable = lambda: None
        self._send_tasks: list[asyncio.Task] = []

    async def start(self) -> None:
        mcp_server = build_orchestrator_mcp_server(
            self._manager,
            apply_layout=self._apply_layout,
            layouts_store=self._layouts_store,
            config_store=self._config_store,
            actions=self._actions,
            rebind_keys=self._rebind_keys,
            widget_registry=self._widget_registry,
            current_layout=self._current_layout,
        )
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
        self._unsub_notify = self._bus.subscribe(
            AgentNotifiedOrchestrator, self._on_child_notified
        )
        self._unsub_ask = self._bus.subscribe(
            AgentRequestedUserInput, self._on_child_asked
        )

    async def wait_idle(self) -> None:
        # queue_send eagerly clears _idle_event synchronously, so we no longer
        # need sleep yields to drain the create_task scheduling gap.
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
        self._unsub_notify()
        self._unsub_ask()
        await self._inner.stop()

    # --- internals --------------------------------------------------------

    def _on_user_message(self, event: UserMessageToOrchestrator) -> None:
        # Prune any tasks that have already completed before adding a new one.
        self._send_tasks = [t for t in self._send_tasks if not t.done()]
        # Use the inner session's queue_send: it eagerly clears _idle_event so
        # wait_idle correctly blocks even before the create_task starts running.
        task = self._inner.queue_send(event.text)
        self._send_tasks.append(task)

    def _on_message_appended(self, event: AgentMessageAppended) -> None:
        if event.agent_id != self.AGENT_ID:
            return
        # RichTranscript subscribes to AgentMessageAppended directly for tool
        # use/result/thinking — only re-publish assistant text, which is the
        # public "the orchestrator said something" signal other code asserts on.
        if event.role == "assistant":
            self._bus.publish(OrchestratorReply(event.text))

    def _on_child_notified(self, event: AgentNotifiedOrchestrator) -> None:
        synthetic = (
            f"[from agent {event.agent_id}] {event.message}"
        )
        self._bus.publish(UserMessageToOrchestrator(synthetic))

    def _on_child_asked(self, event: AgentRequestedUserInput) -> None:
        synthetic = (
            f"[agent {event.agent_id} is blocked waiting for your reply, "
            f"request_id={event.request_id}] question: {event.question}\n"
            f"Use respond_to_agent_request(agent_id={event.agent_id!r}, "
            f"request_id={event.request_id!r}, response=...) to unblock."
        )
        self._bus.publish(UserMessageToOrchestrator(synthetic))
