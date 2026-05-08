import asyncio
import dataclasses
import time
import uuid
from pathlib import Path
from typing import Callable

from claude_agent_sdk import (
    CanUseTool,
    ClaudeAgentOptions,
    PermissionResultAllow,
    PermissionResultDeny,
    ToolPermissionContext,
)

from patchfeld.agents.child_tools import build_child_mcp_server
from patchfeld.agents.permission_grants import PermissionGrants
from patchfeld.agents.permission_inbox import PermissionInbox
from patchfeld.agents.request_inbox import RequestInbox
from patchfeld.agents.sdk_adapter import SDKAdapter
from patchfeld.agents.session import AgentSession
from patchfeld.agents.state import AgentInfo, AgentState
from patchfeld.events import (
    AgentArchiveChanged,
    AgentSpawned,
    AgentStateChanged,
    DirectMessageToAgent,
    EventBus,
    PermissionRequested,
    PermissionResolved,
)
from patchfeld.persistence.agents_index import AgentsIndex
from patchfeld.persistence.transcript_store import AgentTranscript, TranscriptEntry


class AgentManager:
    """Owns child AgentSessions: spawn / list / read transcript / interrupt / kill."""

    def __init__(
        self,
        *,
        cwd: Path,
        bus: EventBus,
        adapter_factory: Callable[[], SDKAdapter],
        permission_grants: PermissionGrants | None = None,
    ) -> None:
        self._cwd = cwd
        self._bus = bus
        self._adapter_factory = adapter_factory
        self._grants = permission_grants
        self._sessions: dict[str, AgentSession] = {}
        self._inboxes: dict[str, RequestInbox] = {}
        self._perm_inboxes: dict[str, PermissionInbox] = {}
        self._index = AgentsIndex(cwd=cwd)
        # Any agent persisted as still-running belongs to a previous (dead)
        # process. Flip those rows to ERROR so the AgentTable seed doesn't
        # show ghosts as live.
        self._index.reconcile_orphans()
        self._unsub_state = bus.subscribe(AgentStateChanged, self._on_state_changed)
        self._unsub_direct = bus.subscribe(DirectMessageToAgent, self._on_direct_message)

    async def spawn(
        self,
        *,
        name: str,
        prompt: str,
        cwd: str | None = None,
        allowed_tools: list[str] | None = None,
        disallowed_tools: list[str] | None = None,
        model: str | None = None,
        system_prompt: str | None = None,
    ) -> str:
        agent_id = uuid.uuid4().hex[:12]
        now = time.time()
        # Snapshot the JSON-serializable subset of options needed to rebuild
        # ClaudeAgentOptions on resume. mcp_servers can't be persisted (it
        # contains live server objects); we rebuild it from agent_id+bus+inbox
        # in _build_options instead.
        spawn_options = {
            "cwd": cwd or str(self._cwd),
            "allowed_tools": allowed_tools,
            "disallowed_tools": disallowed_tools,
            "model": model,
            "system_prompt": system_prompt,
        }
        info = AgentInfo(
            id=agent_id,
            name=name,
            cwd=cwd or str(self._cwd),
            started_at=now,
            spawn_options=spawn_options,
        )
        session = self._build_session(info)
        self._index.upsert(info)
        self._bus.publish(AgentSpawned(info=info))

        await session.start(options=self._build_options(info))
        await session.send(prompt)
        return agent_id

    def _build_session(self, info: AgentInfo) -> AgentSession:
        adapter = self._adapter_factory()
        transcript = AgentTranscript(cwd=self._cwd, agent_id=info.id)
        session = AgentSession(
            info=info,
            adapter=adapter,
            transcript=transcript,
            bus=self._bus,
            on_session_id=lambda sid, _id=info.id: self._on_session_id(_id, sid),
        )
        # Inbox lifecycle drives session state: count > 0 → WAITING,
        # count == 0 → restore prior state. _mark_unwaiting is defensively
        # a no-op outside WAITING, so a `kill()` mid-wait that drains the
        # future after the session is gone is safe.
        def _on_pending_changed(count: int, _session=session) -> None:
            if count > 0:
                _session._mark_waiting()
            else:
                _session._mark_unwaiting()

        self._inboxes[info.id] = RequestInbox(
            on_pending_changed=_on_pending_changed,
        )

        def _on_perm_changed(count: int, _session=session) -> None:
            if count > 0:
                _session._mark_awaiting_permission()
            else:
                _session._mark_done_permission()

        self._perm_inboxes[info.id] = PermissionInbox(
            on_pending_changed=_on_perm_changed,
        )
        self._sessions[info.id] = session
        return session

    def _build_options(
        self, info: AgentInfo, *, resume_session_id: str | None = None,
    ) -> ClaudeAgentOptions:
        # Permission posture: presence of self._grants is the gate.
        #   - None  → permission_mode="bypassPermissions" (preserves the
        #     original behavior; equivalent to launching with
        #     --bypass-permissions).
        #   - obj   → drop bypass, attach can_use_tool that consults the
        #     grants store first and falls back to the modal flow.
        child_mcp = build_child_mcp_server(
            agent_id=info.id, bus=self._bus, inbox=self._inboxes[info.id],
        )
        opts = info.spawn_options or {}
        kwargs: dict = {
            "cwd": opts.get("cwd") or info.cwd,
            "mcp_servers": {"patchfeld_child": child_mcp},
        }
        if self._grants is None:
            kwargs["permission_mode"] = "bypassPermissions"
        else:
            kwargs["can_use_tool"] = self._make_can_use_tool(
                agent_id=info.id, agent_name=info.name,
            )
        if opts.get("allowed_tools") is not None:
            kwargs["allowed_tools"] = opts["allowed_tools"]
        if opts.get("disallowed_tools") is not None:
            kwargs["disallowed_tools"] = opts["disallowed_tools"]
        if opts.get("model") is not None:
            kwargs["model"] = opts["model"]
        if opts.get("system_prompt") is not None:
            kwargs["system_prompt"] = opts["system_prompt"]
        if resume_session_id is not None:
            kwargs["resume"] = resume_session_id
        return ClaudeAgentOptions(**kwargs)

    def _make_can_use_tool(self, *, agent_id: str, agent_name: str) -> CanUseTool:
        bus = self._bus
        grants = self._grants
        get_perm_inbox = self._perm_inboxes.get
        # 30 minutes — long enough to step away briefly, short enough that a
        # forgotten prompt doesn't strand the session forever.
        TIMEOUT_S = 30 * 60

        async def callback(
            tool_name: str,
            tool_input: dict,
            ctx: ToolPermissionContext,
        ):
            assert grants is not None  # invariant when callback is wired
            decision = grants.lookup(agent_name=agent_name, tool_name=tool_name)
            if decision == "allow":
                return PermissionResultAllow()
            if decision == "deny":
                return PermissionResultDeny(message="denied by saved rule")

            inbox = get_perm_inbox(agent_id)
            if inbox is None:
                return PermissionResultDeny(message="agent gone", interrupt=True)
            request_id = inbox.register(
                tool_name=tool_name, tool_input=tool_input,
                title=getattr(ctx, "title", None),
                description=getattr(ctx, "description", None),
            )
            bus.publish(PermissionRequested(
                agent_id=agent_id, agent_name=agent_name,
                request_id=request_id, tool_name=tool_name,
                tool_input=tool_input,
                title=getattr(ctx, "title", None),
                description=getattr(ctx, "description", None),
            ))
            try:
                result = await inbox.wait(request_id, timeout_s=TIMEOUT_S)
            except asyncio.CancelledError:
                task = asyncio.current_task()
                if task is not None and task.cancelling() > 0:
                    raise  # real task cancellation — must propagate
                bus.publish(PermissionResolved(
                    agent_id=agent_id, request_id=request_id,
                    behavior="cancelled",
                ))
                return PermissionResultDeny(message="cancelled", interrupt=True)
            except asyncio.TimeoutError:
                bus.publish(PermissionResolved(
                    agent_id=agent_id, request_id=request_id, behavior="deny",
                ))
                return PermissionResultDeny(message="timed out")
            bus.publish(PermissionResolved(
                agent_id=agent_id, request_id=request_id,
                behavior="allow" if isinstance(result, PermissionResultAllow) else "deny",
            ))
            return result

        return callback

    def _on_session_id(self, agent_id: str, session_id: str) -> None:
        # The first ResultMessage carries the SDK session id. Capture it on
        # the persisted info so a fresh process can pass it back as resume=
        # to keep the conversation alive.
        session = self._sessions.get(agent_id)
        if session is None:
            return
        session.info.session_id = session_id
        self._index.upsert(session.info)

    async def resume(self, agent_id: str) -> AgentSession | None:
        # If the agent already has a live session, just hand it back.
        existing = self._sessions.get(agent_id)
        if existing is not None:
            return existing
        # Find the persisted record. If it's missing, or it predates the
        # resume feature (no session_id / no spawn_options), we can't bring
        # it back to life — caller must spawn fresh.
        for info in self._index.load():
            if info.id == agent_id:
                target = info
                break
        else:
            return None
        if target.session_id is None or target.spawn_options is None:
            return None
        # Resurrect: fresh adapter, AgentSession, and SDK process pointed at
        # the same session_id so the conversation continues.
        target.state = AgentState.IDLE
        target.ended_at = None
        session = self._build_session(target)
        self._index.upsert(target)
        self._bus.publish(AgentSpawned(info=target))
        await session.start(
            options=self._build_options(target, resume_session_id=target.session_id),
        )
        return session

    def list_infos(self) -> list[AgentInfo]:
        return [s.info for s in self._sessions.values()]

    def get_session(self, agent_id: str) -> AgentSession | None:
        return self._sessions.get(agent_id)

    def read_transcript(self, agent_id: str) -> list[TranscriptEntry]:
        path_transcript = AgentTranscript(cwd=self._cwd, agent_id=agent_id)
        return path_transcript.read_all()

    async def interrupt(self, agent_id: str) -> None:
        session = self._sessions.get(agent_id)
        if session is not None:
            await session.interrupt()

    async def kill(self, agent_id: str) -> None:
        session = self._sessions.pop(agent_id, None)
        self._inboxes.pop(agent_id, None)
        perm_inbox = self._perm_inboxes.pop(agent_id, None)
        if perm_inbox is not None:
            perm_inbox.cancel_all()
        if session is not None:
            await session.stop()

    async def wait_idle(self, agent_id: str) -> None:
        session = self._sessions.get(agent_id)
        if session is not None:
            await session.wait_idle()

    async def send(self, agent_id: str, text: str) -> None:
        session = self._sessions.get(agent_id)
        if session is None:
            raise KeyError(f"unknown agent_id: {agent_id}")
        await session.send(text)

    def get_inbox(self, agent_id: str) -> RequestInbox | None:
        return self._inboxes.get(agent_id)

    def get_permission_inbox(self, agent_id: str) -> PermissionInbox | None:
        return self._perm_inboxes.get(agent_id)

    def set_archived(self, agent_id: str, *, archived: bool) -> None:
        """Toggle the archived flag for an agent. Persists to agents.json and
        publishes AgentArchiveChanged so listeners (e.g., AgentTable) can
        refresh. Raises KeyError if `agent_id` is unknown."""
        # The archived flag is metadata, not runtime state — agents from a
        # previous process show up in the table (seeded from agents.json) but
        # have no live session here. Fall back to the persisted record so
        # archive/unarchive works on those rows too.
        session = self._sessions.get(agent_id)
        if session is not None:
            info = session.info
        else:
            info = next(
                (i for i in self._index.load() if i.id == agent_id), None
            )
            if info is None:
                raise KeyError(f"unknown agent_id: {agent_id}")
        if info.archived == archived:
            return
        info.archived = archived
        self._index.upsert(info)
        # Publish a frozen snapshot so subscribers see a stable view.
        self._bus.publish(AgentArchiveChanged(info=dataclasses.replace(info)))

    async def shutdown(self) -> None:
        for agent_id in list(self._sessions.keys()):
            await self.kill(agent_id)
        self._unsub_state()
        self._unsub_direct()

    # --- internals --------------------------------------------------------

    def _on_state_changed(self, event: AgentStateChanged) -> None:
        # Persist updated info on every state change so agents.json reflects reality.
        self._index.upsert(event.info)

    def _on_direct_message(self, event: DirectMessageToAgent) -> None:
        session = self._sessions.get(event.agent_id)
        if session is not None:
            session.queue_send(event.text)
            return
        # No live session: this is a record from a previous process. Try to
        # resurrect it via SDK resume so the user's message lands on a real
        # conversation. Schedule on the running loop because EventBus
        # handlers must be sync.
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # no loop — nothing we can do (e.g. unit-test without loop)
        loop.create_task(self._resume_then_send(event.agent_id, event.text))

    async def _resume_then_send(self, agent_id: str, text: str) -> None:
        session = await self.resume(agent_id)
        if session is None:
            return  # legacy record without session_id/spawn_options
        session.queue_send(text)
