from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container

from mod_tui.events import EventBus, OrchestratorReply, UserMessageToOrchestrator
from mod_tui.layout.defaults import dashboard_layout
from mod_tui.layout.engine import apply as apply_layout
from mod_tui.layout.registry import WidgetRegistry
from mod_tui.layout.spec import LayoutSpec
from mod_tui.persistence.layout_store import load_layout, save_layout
from mod_tui.persistence.transcript_store import OrchestratorTranscript, TranscriptEntry
from mod_tui.widgets.chrome import CommandBar, StatusBar
from mod_tui.widgets.orchestrator_chat import OrchestratorChat
from mod_tui.widgets.placeholders import ActivityFeed, AgentTable


class _FakeOrchestratorSession:
    """Temporary echo stand-in until app.py is wired to OrchestratorSession (Task 15)."""

    def __init__(self, *, bus: EventBus, transcript: OrchestratorTranscript | None) -> None:
        self._bus = bus
        self._transcript = transcript
        self._unsub = lambda: None

    def start(self) -> None:
        self._unsub = self._bus.subscribe(UserMessageToOrchestrator, self._handle)

    def stop(self) -> None:
        self._unsub()

    def _handle(self, event: UserMessageToOrchestrator) -> None:
        if self._transcript is not None:
            self._transcript.append(TranscriptEntry(role="user", text=event.text))
        reply = f"I heard: {event.text}"
        self._bus.publish(OrchestratorReply(reply))
        if self._transcript is not None:
            self._transcript.append(TranscriptEntry(role="orch", text=reply))


def build_default_registry() -> WidgetRegistry:
    reg = WidgetRegistry()
    reg.register("OrchestratorChat", OrchestratorChat)
    reg.register("AgentTable", AgentTable)
    reg.register("ActivityFeed", ActivityFeed)
    return reg


class ModTuiApp(App):
    """Walking-skeleton App. Real Claude Agent SDK wiring lives in plan 2."""

    CSS = """
    #panel-area {
        height: 1fr;
    }
    """

    # Tab/shift-tab cycle focus via Textual's built-in focus chain — no
    # explicit binding needed. ctrl-h (history) and ctrl-l (layout switcher)
    # ship in plan 4 alongside the features they open.
    BINDINGS = [
        Binding("/", "focus_command_bar", "command bar", priority=True),
        Binding("ctrl+q", "quit", "quit"),
        Binding("?", "show_help", "help"),
    ]

    def __init__(self, *, cwd: Path | None = None,
                 registry: WidgetRegistry | None = None) -> None:
        super().__init__()
        self.cwd = Path(cwd) if cwd else Path.cwd()
        self.event_bus = EventBus()
        self.registry = registry or build_default_registry()
        self._current_spec: LayoutSpec | None = None

        self.transcript = OrchestratorTranscript(cwd=self.cwd)
        self.session = _FakeOrchestratorSession(
            bus=self.event_bus, transcript=self.transcript
        )
        # Make prior history available to the OrchestratorChat widget at mount.
        self.orchestrator_history: list[tuple[str, str]] = [
            (e.role, e.text) for e in self.transcript.read_all()
        ]

    def compose(self) -> ComposeResult:
        yield CommandBar(event_bus=self.event_bus)
        yield Container(id="panel-area")
        yield StatusBar(event_bus=self.event_bus)

    async def on_mount(self) -> None:
        self.session.start()
        spec = load_layout(self.cwd) or dashboard_layout()
        await self._apply(spec)

    async def _apply(self, spec: LayoutSpec) -> None:
        area = self.query_one("#panel-area", Container)
        await apply_layout(area, spec, self.registry)
        self._current_spec = spec
        save_layout(self.cwd, spec)

    def on_unmount(self) -> None:
        self.session.stop()

    def action_focus_command_bar(self) -> None:
        self.query_one(CommandBar).focus_input()

    def action_show_help(self) -> None:
        self.notify(
            "/ command bar · ctrl-q quit · ? help",
            title="keybindings",
        )
