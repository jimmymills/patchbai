from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import Input

from patchfeld.events import DirectMessageToAgent, EventBus, PermissionRequested, PermissionResolved
from patchfeld.widgets.rich_transcript import RichTranscript


class AgentTranscript(Vertical):
    """Per-child-agent transcript panel: RichTranscript + input box."""

    DEFAULT_CSS = """
    AgentTranscript {
        border: round $surface-lighten-2;
        padding: 0 1;
    }
    AgentTranscript > RichTranscript {
        height: 1fr;
    }
    AgentTranscript #transcript-input {
        dock: bottom;
        height: 3;
    }
    """

    # priority=True so the binding fires even when the Input child has
    # focus — without it, ctrl+c is consumed by Textual's default driver
    # handling (which would quit the app). Mirrors OrchestratorChat so
    # both chat panels respond to ctrl+c the same way.
    BINDINGS = [
        Binding("ctrl+c", "interrupt", "interrupt agent", priority=True),
    ]

    def __init__(
        self,
        *,
        agent_id: str,
        event_bus: EventBus | None = None,
    ) -> None:
        super().__init__()
        self._agent_id = agent_id
        self._bus = event_bus
        self._unsub_perm_req = lambda: None
        self._unsub_perm_res = lambda: None

    def compose(self) -> ComposeResult:
        yield RichTranscript(agent_id=self._agent_id, event_bus=self._bus)
        yield Input(placeholder=f"Message {self._agent_id}…", id="transcript-input")

    def on_mount(self) -> None:
        bus = self._bus or getattr(self.app, "event_bus", None)
        if bus is not None:
            self._unsub_perm_req = bus.subscribe(
                PermissionRequested, self._on_perm_request, agent_id=self._agent_id,
            )
            self._unsub_perm_res = bus.subscribe(
                PermissionResolved, self._on_perm_resolved, agent_id=self._agent_id,
            )

    def on_unmount(self) -> None:
        self._unsub_perm_req()
        self._unsub_perm_res()

    def _on_perm_request(self, event: PermissionRequested) -> None:
        # Bus-level keyed subscription guarantees event.agent_id == self._agent_id.
        from patchfeld.widgets.permission_request_bar import PermissionRequestBar
        bar = PermissionRequestBar(request=event)
        self.mount(bar, before=self.query_one(RichTranscript))

    def _on_perm_resolved(self, event: PermissionResolved) -> None:
        # Bus-level keyed subscription guarantees event.agent_id == self._agent_id.
        from patchfeld.widgets.permission_request_bar import PermissionRequestBar
        for bar in list(self.query(PermissionRequestBar)):
            if bar.request_id == event.request_id:
                bar.remove()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return
        bus = self._bus or getattr(self.app, "event_bus", None)
        if bus is not None:
            bus.publish(DirectMessageToAgent(agent_id=self._agent_id, text=text))
        event.input.value = ""

    async def action_interrupt(self) -> None:
        manager = getattr(self.app, "manager", None)
        if manager is None:
            return
        # Stale-panel guard: if no live session matches this panel's
        # agent_id, manager.interrupt would silently no-op. Surface that
        # to the user instead — otherwise ctrl+c looks broken when in
        # fact the panel is bound to an agent that no longer exists
        # (e.g. opened from a stale agents.json entry).
        if manager.get_session(self._agent_id) is None:
            try:
                self.app.notify(
                    f"no active agent “{self._agent_id}” — panel may be stale",
                    severity="warning", timeout=5,
                )
            except Exception:
                pass
            return
        await manager.interrupt(self._agent_id)
        try:
            self.app.notify(
                f"interrupted {self._agent_id}", timeout=3,
            )
        except Exception:
            pass

    def rendered_text(self) -> str:
        """Test helper — delegates to the inner RichTranscript."""
        return self.query_one(RichTranscript).rendered_text()

    @classmethod
    def default_border_title(cls, props: dict) -> str:
        agent_id = props.get("agent_id")
        if agent_id:
            return f"Agent: {agent_id}"
        return "Agent"
