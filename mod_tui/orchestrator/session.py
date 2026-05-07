import asyncio
import logging
import time
import uuid
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions

from mod_tui.agents.manager import AgentManager
from mod_tui.agents.sdk_adapter import RealSDKAdapter, SDKAdapter
from mod_tui.agents.session import AgentSession
from mod_tui.agents.state import AgentInfo
from mod_tui.events import (
    AgentMessageAppended,
    AgentNotifiedOrchestrator,
    AgentRequestedUserInput,
    EventBus,
    OrchestratorReply,
    UserMessageToOrchestrator,
)
from mod_tui.orchestrator.tools import build_orchestrator_mcp_server
from mod_tui.persistence.orchestrator_sessions import (
    OrchestratorSessionEntry,
    OrchestratorSessionsIndex,
)
from mod_tui.persistence.paths import orchestrator_session_transcript_path
from mod_tui.persistence.transcript_store import AgentTranscript

log = logging.getLogger(__name__)


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
        app=None,
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
        self._app = app
        self._index = OrchestratorSessionsIndex(cwd=cwd)
        self._sdk_session_id: str | None = None
        self._active_transcript_path: Path | None = None
        self._switching_lock = asyncio.Lock()
        self._info = AgentInfo(
            id=self.AGENT_ID,
            name="orchestrator",
            cwd=str(cwd),
            started_at=time.time(),
        )
        self._inner: AgentSession | None = None  # built in start()
        self._unsub_user: callable = lambda: None
        self._unsub_msg: callable = lambda: None
        self._unsub_notify: callable = lambda: None
        self._unsub_ask: callable = lambda: None
        self._send_tasks: list[asyncio.Task] = []

    @property
    def active_transcript_path(self) -> "Path | None":
        return self._active_transcript_path

    async def start(self) -> None:
        # One-time migration of any pre-existing orchestrator.jsonl.
        self._index.migrate_legacy_if_needed()

        # Decide: resume vs new
        prior = self._index.most_recent()
        resume_id: str | None = None
        if prior is not None and not prior.legacy:
            resume_id = prior.session_id
            session_id_for_options = None
            transcript_path = orchestrator_session_transcript_path(
                self._cwd, prior.session_id
            )
            self._sdk_session_id = prior.session_id
        else:
            new_id = uuid.uuid4().hex
            session_id_for_options = new_id
            transcript_path = orchestrator_session_transcript_path(self._cwd, new_id)
            self._sdk_session_id = new_id
        self._active_transcript_path = transcript_path

        await self._build_and_start_inner(
            resume=resume_id, new_session_id=session_id_for_options,
            transcript_path=transcript_path,
        )

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

    async def _build_and_start_inner(
        self,
        *,
        resume: str | None,
        new_session_id: str | None,
        transcript_path: Path,
    ) -> None:
        mcp_server = build_orchestrator_mcp_server(
            self._manager,
            apply_layout=self._apply_layout,
            layouts_store=self._layouts_store,
            config_store=self._config_store,
            actions=self._actions,
            rebind_keys=self._rebind_keys,
            widget_registry=self._widget_registry,
            current_layout=self._current_layout,
            app=self._app,
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
        if resume is not None:
            options_kwargs["resume"] = resume
        if new_session_id is not None:
            options_kwargs["session_id"] = new_session_id
        if self._model is not None:
            options_kwargs["model"] = self._model

        transcript = AgentTranscript(
            cwd=self._cwd, agent_id=self.AGENT_ID, path=transcript_path,
        )
        self._inner = AgentSession(
            info=self._info,
            adapter=self._adapter,
            transcript=transcript,
            bus=self._bus,
            on_session_id=self._on_session_id_observed,
        )
        await self._inner.start(options=ClaudeAgentOptions(**options_kwargs))

    def _on_session_id_observed(self, session_id: str) -> None:
        # Update in-memory pointer to whatever the SDK actually attached us to.
        if self._sdk_session_id != session_id:
            log.warning(
                "orchestrator session_id mismatch: passed %s observed %s",
                self._sdk_session_id, session_id,
            )
            self._sdk_session_id = session_id
            # Note: _active_transcript_path is NOT re-pointed here — the
            # AgentTranscript was already opened at the original path and
            # all writes go there. We keep _active_transcript_path stable
            # so callers (e.g. OrchestratorChat) can read from the right file.

        existing = self._index.get(session_id)
        now = time.time()
        if existing is None:
            entry = OrchestratorSessionEntry(
                session_id=session_id,
                transcript_path=str(self._active_transcript_path),
                started_at=self._info.started_at,
                last_activity=now,
                first_user_message=None,
                num_turns=0,
                tokens_in=self._info.tokens_in,
                tokens_out=self._info.tokens_out,
                cost=self._info.cost,
                legacy=False,
            )
        else:
            existing.last_activity = now
            existing.tokens_in = self._info.tokens_in
            existing.tokens_out = self._info.tokens_out
            existing.cost = self._info.cost
            entry = existing
        self._index.upsert(entry)

    async def interrupt(self) -> None:
        """Cancel the SDK's currently-running query, if any.

        Safe to call when the orchestrator is idle — the underlying
        adapter's interrupt is a no-op in that case.
        """
        if self._inner is not None:
            await self._inner.interrupt()

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
        if self._inner is not None:
            await self._inner.wait_idle()

    async def stop(self) -> None:
        self._unsub_user()
        self._unsub_msg()
        self._unsub_notify()
        self._unsub_ask()
        if self._inner is not None:
            await self._inner.stop()

    # --- internals --------------------------------------------------------

    def _on_user_message(self, event: UserMessageToOrchestrator) -> None:
        if self._inner is None:
            return
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
