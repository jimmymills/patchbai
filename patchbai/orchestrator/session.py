import asyncio
import logging
import re
import time
import uuid
from pathlib import Path
from typing import Callable

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    TextBlock,
    query as sdk_query,
)
from claude_agent_sdk.types import SystemPromptPreset

from patchbai.agents.manager import AgentManager
from patchbai.agents.sdk_adapter import RealSDKAdapter, SDKAdapter
from patchbai.agents.session import AgentSession
from patchbai.agents.state import AgentInfo
from patchbai.events import (
    AgentMessageAppended,
    AgentNotifiedOrchestrator,
    AgentRequestedUserInput,
    AgentTokensTouched,
    EventBus,
    OpenResumePicker,
    OrchestratorReply,
    OrchestratorSessionSwitched,
    UserMessageToOrchestrator,
)

from patchbai.orchestrator.tools import build_orchestrator_mcp_server
from patchbai.persistence.orchestrator_sessions import (
    OrchestratorSessionEntry,
    OrchestratorSessionsIndex,
)
from patchbai.persistence.paths import orchestrator_session_transcript_path
from patchbai.persistence.transcript_store import AgentTranscript

log = logging.getLogger(__name__)

_RESET_RE = re.compile(r"^/reset(?:\s|$)")
_RESUME_BARE_RE = re.compile(r"^/resume\s*$")
_RESUME_ID_RE = re.compile(r"^/resume\s+(\S+)\s*$")
# /rename <title>  — current session
# /rename <session_id> <title>  — specific session (id is non-space; title is rest)
_RENAME_RE = re.compile(r"^/rename(?:\s+(.*))?$")
_HELP_RE = re.compile(r"^/help\s*$")
_CD_RE = re.compile(r"^/cd\s+(.+?)\s*$")

_HELP_TEXT = (
    "Available commands:\n"
    "  /reset                     Start a fresh orchestrator session\n"
    "  /resume [<session_id>]     Resume a past session (no arg → picker)\n"
    "  /rename [<id>] <title>     Rename the active or a specific session\n"
    "  /cd <path>                 Re-root the workspace at <path>\n"
    "  /help                      Show this list"
)

_TITLE_PROMPT = (
    "Summarize the following user message in 5-7 words for use as a session "
    "title. Respond with ONLY the title — no quotes, no punctuation, no "
    "preamble. Message:\n\n{message}"
)

# Appended to the default Claude Code system prompt for the orchestrator
# session. It tells the model to reach for the patchbai MCP tools (the ones
# registered on the `patchbai_orchestrator` MCP server, visible to the model
# as `mcp__patchbai_orchestrator__<name>`) before falling back to generic
# Bash/Edit/Write/Read/Grep when a patchbai tool already covers the job.
_ORCHESTRATOR_SYSTEM_APPEND = """\
## Tool Preference (patchbai)

You are running inside the patchbai TUI. A `patchbai_orchestrator` MCP server
exposes tools (visible as `mcp__patchbai_orchestrator__<name>`) that mutate
the running app safely. **When a patchbai tool can accomplish the task, call
it before falling back to Bash/Edit/Write/Read/Grep.** Editing the underlying
files or shelling out usually requires a restart and can desync the live UI.

- Layout / tabs: prefer `set_layout`, `get_layout`, `add_tab`, `close_tab`,
  `switch_tab`, `rename_tab`, `reorder_tabs`, `save_layout`, `load_layout`,
  `list_layouts`, `list_tabs`, `list_widgets` over editing workspace.json or
  layout files by hand.
- Agents: prefer `spawn_agent`, `kill_agent`, `interrupt_agent`,
  `send_to_agent`, `read_agent_transcript`, `respond_to_agent_request`,
  `list_agents` over restarting the app or grepping log files.
- Workspace cwd: prefer `change_cwd` over editing config files or telling
  the user to relaunch.
- Theme / config / keys: prefer `set_theme`, `save_theme`, `load_theme`,
  `set_config`, `bind_key`, `unbind_key` over editing config.toml or theme
  files directly.
- Custom widgets: prefer `save_widget` (persists to
  ~/.config/patchbai/widgets/ and registers live for use in the same
  conversation) over `Write`-ing the file via the generic tool. For
  one-off, throwaway widgets that should NOT be persisted, embed the
  source in `LayoutSpec.custom_widgets` instead.

Generic tools (Bash, Edit, Write, Read, Grep) remain appropriate for
arbitrary source-code edits, running tests, git operations, and anything
no patchbai tool covers.
"""


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
        themes_store=None,
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
        self._themes_store = themes_store
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
        self._current_session_first_message: str | None = None
        self._current_session_num_turns: int = 0
        self._inner: AgentSession | None = None  # built in start()
        self._unsub_user: callable = lambda: None
        self._unsub_msg: callable = lambda: None
        self._unsub_notify: callable = lambda: None
        self._unsub_ask: callable = lambda: None
        # Test-only seam: when set, used as the adapter factory for the next
        # swap (during /reset or /resume). Production wiring uses RealSDKAdapter.
        self._next_adapter_factory: "Callable[[], SDKAdapter] | None" = None
        self._send_tasks: list[asyncio.Task] = []
        # Production sets this to True after construction so new sessions
        # get auto-titled. Defaults to False so tests don't spawn real Claude
        # CLI subprocesses.
        self._auto_title_enabled: bool = False
        self._title_task: asyncio.Task | None = None

    @property
    def active_transcript_path(self) -> "Path | None":
        return self._active_transcript_path

    @property
    def info(self) -> AgentInfo:
        """The AgentInfo accumulating tokens, cost, and last-activity for the
        orchestrator's own SDK session. Shared by reference with the inner
        AgentSession, so reads always reflect the latest counters."""
        return self._info

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
            # Canonical UUID form (8-4-4-4-12) is what Claude CLI expects on
            # --session-id; bare hex (uuid.hex) is rejected at startup.
            new_id = str(uuid.uuid4())
            session_id_for_options = new_id
            transcript_path = orchestrator_session_transcript_path(self._cwd, new_id)
            self._sdk_session_id = new_id
        self._active_transcript_path = transcript_path

        await self._build_and_start_inner(
            resume=resume_id, new_session_id=session_id_for_options,
            transcript_path=transcript_path,
        )
        # Seed per-session counters from the resumed entry (or zero for a
        # fresh start) so the StatusBar reflects the running total of the
        # conversation we just attached to.
        self._seed_counters_from(prior if resume_id is not None else None)

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
            themes_store=self._themes_store,
            config_store=self._config_store,
            actions=self._actions,
            rebind_keys=self._rebind_keys,
            widget_registry=self._widget_registry,
            current_layout=self._current_layout,
            app=self._app,
        )
        options_kwargs: dict = {
            "cwd": str(self._cwd),
            "mcp_servers": {"patchbai_orchestrator": mcp_server},
            # The orchestrator is the user's trusted manager session — there's
            # no UI in the TUI yet to render a permission prompt, so the SDK
            # would hang waiting for one. Bypass for now; a Textual modal-
            # based can_use_tool callback is plan-3 work.
            "permission_mode": "bypassPermissions",
            # Append a routing nudge to the default Claude Code system prompt
            # so the model reaches for patchbai_orchestrator MCP tools before
            # falling back to Bash/Edit/Write/Read/Grep when a patchbai tool
            # already covers the task.
            "system_prompt": SystemPromptPreset(
                type="preset",
                preset="claude_code",
                append=_ORCHESTRATOR_SYSTEM_APPEND,
            ),
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
        is_new_entry = existing is None
        if existing is None:
            entry = OrchestratorSessionEntry(
                session_id=session_id,
                transcript_path=str(self._active_transcript_path),
                started_at=self._info.started_at,
                last_activity=now,
                first_user_message=self._current_session_first_message,
                num_turns=self._current_session_num_turns,
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

        # New session + first prompt available → fire async title summarizer.
        if (
            is_new_entry
            and self._auto_title_enabled
            and entry.title is None
            and self._current_session_first_message
        ):
            self._title_task = asyncio.create_task(
                self._generate_title_async(
                    session_id, self._current_session_first_message,
                )
            )

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
        text = event.text
        # Slash-command interception. Only triggers on bare-prefix matches —
        # synthetic messages from child agents are wrapped in "[from agent ...]"
        # and so cannot match.
        if _RESET_RE.match(text):
            self._send_tasks = [t for t in self._send_tasks if not t.done()]
            self._send_tasks.append(asyncio.create_task(self.reset()))
            return
        if _RESUME_BARE_RE.match(text):
            self._bus.publish(OpenResumePicker())
            return
        m = _RESUME_ID_RE.match(text)
        if m:
            self._send_tasks = [t for t in self._send_tasks if not t.done()]
            self._send_tasks.append(asyncio.create_task(self.resume(m.group(1))))
            return
        m = _RENAME_RE.match(text)
        if m:
            self._handle_rename_command(m.group(1) or "")
            return
        m = _CD_RE.match(text)
        if m and self._app is not None:
            path = m.group(1).strip()
            self._send_tasks = [t for t in self._send_tasks if not t.done()]
            self._send_tasks.append(
                asyncio.create_task(self._handle_cd_command(path))
            )
            return
        if _HELP_RE.match(text):
            self._publish_notice(_HELP_TEXT)
            return
        # Fall through: ordinary prompt.
        if self._current_session_first_message is None:
            self._current_session_first_message = text
        self._send_tasks = [t for t in self._send_tasks if not t.done()]
        task = self._inner.queue_send(text)
        self._send_tasks.append(task)

    def _handle_rename_command(self, args: str) -> None:
        """Handle /rename invocations.

        Forms:
          /rename                       → notice on missing title
          /rename <title>               → renames the active session
          /rename <session_id> <title>  → renames a specific session
        """
        args = args.strip()
        if not args:
            self._publish_notice(
                "Usage: /rename <new title>  or  /rename <session_id> <title>"
            )
            return
        # If the first token matches a known session_id, treat it as
        # /rename <id> <title>; otherwise the whole thing is the active title.
        first, _, rest = args.partition(" ")
        rest = rest.strip()
        candidate_entry = self._index.get(first)
        if candidate_entry is not None and rest:
            target_id = first
            new_title: str | None = rest
        else:
            target_id = self._sdk_session_id or ""
            new_title = args
        if not target_id:
            self._publish_notice("No active session to rename.")
            return
        if not new_title:
            new_title = None  # explicit clear
        ok = self._index.set_title(target_id, new_title)
        if not ok:
            self._publish_notice(f"No such session: {target_id}")
            return
        label = new_title if new_title else "(cleared)"
        self._publish_notice(f"Renamed session to: {label}")

    async def _handle_cd_command(self, path: str) -> None:
        if self._app is None:
            return
        result = await self._app.change_cwd(path)
        if "error" in result:
            err = result["error"]
            if err == "agents_running":
                names = ", ".join(a["name"] for a in result.get("agents", []))
                self._publish_notice(
                    f"Refusing /cd: agents still running ({names})."
                )
            elif err == "invalid_path":
                self._publish_notice(
                    f"Invalid path: {result.get('path') or result.get('detail')}"
                )
            else:
                self._publish_notice(f"/cd failed: {err}")
        elif "unchanged" in result:
            self._publish_notice("cwd unchanged.")
        else:
            self._publish_notice(f"cwd → {result['changed']}")

    async def _generate_title_async(self, session_id: str, first_user_message: str) -> None:
        """Issue a one-shot SDK query to summarize the first message into a
        5-7 word title. Silently no-ops on any failure."""
        try:
            text = await self._summarize_for_title(first_user_message)
        except Exception:
            log.exception("title summarization failed for %s", session_id)
            return
        if not text:
            return
        # Strip stray quotes/punctuation a model might add despite instructions.
        title = text.strip().strip('"\'').rstrip(".").strip()
        if not title:
            return
        # Cap to a reasonable display length even if the model went over.
        if len(title) > 80:
            title = title[:79] + "…"
        self._index.set_title(session_id, title)

    async def _summarize_for_title(self, first_user_message: str) -> str:
        """One-shot SDK query that returns the model's title text.

        Isolated as its own method so tests can monkeypatch it without
        spawning a real subprocess.
        """
        prompt = _TITLE_PROMPT.format(message=first_user_message)
        options = ClaudeAgentOptions(
            cwd=str(self._cwd),
            permission_mode="bypassPermissions",
            model="claude-haiku-4-5",
        )
        chunks: list[str] = []
        async for msg in sdk_query(prompt=prompt, options=options):
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        chunks.append(block.text)
        return " ".join(chunks).strip()

    async def reset(self) -> None:
        async with self._switching_lock:
            await self._swap_inner(resume=None)
            self._seed_counters_from(None)

    async def resume(self, session_id: str) -> None:
        async with self._switching_lock:
            entry = self._index.get(session_id)
            if entry is None:
                self._publish_notice(f"No such session: {session_id}")
                return
            if entry.legacy:
                self._publish_notice(
                    "This session predates SDK resume support; starting a fresh session."
                )
                await self._swap_inner(resume=None)
                self._seed_counters_from(None)
                return
            try:
                await self._swap_inner(resume=session_id)
                self._seed_counters_from(entry)
            except Exception:
                log.exception("SDK rejected resume=%s; falling back to fresh", session_id)
                self._publish_notice(
                    f"Could not resume {session_id}; starting a fresh session."
                )
                self._inner = None
                await self._swap_inner(resume=None)
                self._seed_counters_from(None)

    def _seed_counters_from(self, entry: "OrchestratorSessionEntry | None") -> None:
        """Reset (entry=None) or seed (entry=resumed entry) the per-session
        token / cost counters on self._info, then publish AgentTokensTouched
        so the StatusBar aggregator picks up the change."""
        if entry is None:
            self._info.tokens_in = 0
            self._info.tokens_out = 0
            self._info.cost = 0.0
        else:
            self._info.tokens_in = entry.tokens_in
            self._info.tokens_out = entry.tokens_out
            self._info.cost = entry.cost
        self._bus.publish(AgentTokensTouched(agent_id=self._info.id))

    def _publish_notice(self, text: str) -> None:
        # Toast for the running app (production UI surface).
        if self._app is not None:
            try:
                self._app.notify(text, title="orchestrator")
            except Exception:
                pass
        # OrchestratorReply event for tests + bus subscribers.
        self._bus.publish(OrchestratorReply(text))

    async def _swap_inner(self, *, resume: str | None) -> None:
        # Stop current, start a new inner with either resume=<id> or a fresh id.
        if self._inner is not None:
            try:
                await self._inner.interrupt()
            except Exception:
                pass
            await self._inner.stop()

        if resume is not None:
            new_session_id = None
            transcript_path = orchestrator_session_transcript_path(self._cwd, resume)
            self._sdk_session_id = resume
        else:
            new_id = str(uuid.uuid4())
            new_session_id = new_id
            transcript_path = orchestrator_session_transcript_path(self._cwd, new_id)
            self._sdk_session_id = new_id
        self._current_session_first_message = None
        self._current_session_num_turns = 0
        self._active_transcript_path = transcript_path

        # Pull a fresh adapter. In production this comes from the
        # RealSDKAdapter factory; tests can inject _next_adapter_factory.
        if self._next_adapter_factory is not None:
            self._adapter = self._next_adapter_factory()
            self._next_adapter_factory = None
        else:
            self._adapter = RealSDKAdapter()

        await self._build_and_start_inner(
            resume=resume, new_session_id=new_session_id,
            transcript_path=transcript_path,
        )

        self._bus.publish(OrchestratorSessionSwitched(
            session_id=self._sdk_session_id,
            transcript_path=str(self._active_transcript_path),
        ))

    def _on_message_appended(self, event: AgentMessageAppended) -> None:
        if event.agent_id != self.AGENT_ID:
            return
        # RichTranscript subscribes to AgentMessageAppended directly for tool
        # use/result/thinking — only re-publish assistant text, which is the
        # public "the orchestrator said something" signal other code asserts on.
        if event.role == "assistant":
            self._bus.publish(OrchestratorReply(event.text))
            self._current_session_num_turns += 1
            self._refresh_session_summary()

    def _refresh_session_summary(self) -> None:
        """Update the index entry for the active session with current
        first_user_message + num_turns + activity. No-op if the session
        hasn't been confirmed (_sdk_session_id not yet observed) or
        if the entry hasn't been created yet (handled by
        _on_session_id_observed)."""
        if self._sdk_session_id is None:
            return
        existing = self._index.get(self._sdk_session_id)
        if existing is None:
            return
        existing.last_activity = time.time()
        # Only set first_user_message if not already set — the very first
        # prompt of the session is the canonical answer; later prompts
        # don't overwrite.
        if existing.first_user_message is None and self._current_session_first_message:
            existing.first_user_message = self._current_session_first_message
        existing.num_turns = max(existing.num_turns, self._current_session_num_turns)
        existing.tokens_in = self._info.tokens_in
        existing.tokens_out = self._info.tokens_out
        existing.cost = self._info.cost
        self._index.upsert(existing)

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
