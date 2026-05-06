import time
import uuid
from pathlib import Path
from typing import Callable

from claude_agent_sdk import ClaudeAgentOptions

from mod_tui.agents.sdk_adapter import SDKAdapter
from mod_tui.agents.session import AgentSession
from mod_tui.agents.state import AgentInfo
from mod_tui.events import (
    AgentSpawned,
    AgentStateChanged,
    EventBus,
)
from mod_tui.persistence.agents_index import AgentsIndex
from mod_tui.persistence.transcript_store import AgentTranscript, TranscriptEntry


class AgentManager:
    """Owns child AgentSessions: spawn / list / read transcript / interrupt / kill."""

    def __init__(
        self,
        *,
        cwd: Path,
        bus: EventBus,
        adapter_factory: Callable[[], SDKAdapter],
    ) -> None:
        self._cwd = cwd
        self._bus = bus
        self._adapter_factory = adapter_factory
        self._sessions: dict[str, AgentSession] = {}
        self._index = AgentsIndex(cwd=cwd)
        self._unsub_state = bus.subscribe(AgentStateChanged, self._on_state_changed)

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
        info = AgentInfo(
            id=agent_id,
            name=name,
            cwd=cwd or str(self._cwd),
            started_at=now,
        )
        adapter = self._adapter_factory()
        transcript = AgentTranscript(cwd=self._cwd, agent_id=agent_id)
        session = AgentSession(
            info=info,
            adapter=adapter,
            transcript=transcript,
            bus=self._bus,
        )
        self._sessions[agent_id] = session
        self._index.upsert(info)
        self._bus.publish(AgentSpawned(info=info))

        # Bypass permissions for now: there's no Textual modal to render
        # the SDK's permission prompts in plan 2, so the child would hang.
        # The orchestrator can still narrow what a child may do via the
        # allowed_tools / disallowed_tools args on the spawn_agent MCP tool.
        # A proper can_use_tool callback that pops a Textual approval modal
        # is plan-3 work.
        options_kwargs: dict = {
            "cwd": info.cwd,
            "permission_mode": "bypassPermissions",
        }
        if allowed_tools is not None:
            options_kwargs["allowed_tools"] = allowed_tools
        if disallowed_tools is not None:
            options_kwargs["disallowed_tools"] = disallowed_tools
        if model is not None:
            options_kwargs["model"] = model
        if system_prompt is not None:
            options_kwargs["system_prompt"] = system_prompt
        await session.start(options=ClaudeAgentOptions(**options_kwargs))
        await session.send(prompt)
        return agent_id

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
        if session is not None:
            await session.stop()

    async def wait_idle(self, agent_id: str) -> None:
        session = self._sessions.get(agent_id)
        if session is not None:
            await session.wait_idle()

    async def shutdown(self) -> None:
        for agent_id in list(self._sessions.keys()):
            await self.kill(agent_id)
        self._unsub_state()

    # --- internals --------------------------------------------------------

    def _on_state_changed(self, event: AgentStateChanged) -> None:
        # Persist updated info on every state change so agents.json reflects reality.
        self._index.upsert(event.info)
