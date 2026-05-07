from typing import Callable

from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static

from patchbai.agents.permission_grants import PermissionGrants
from patchbai.agents.permission_inbox import PermissionInbox
from patchbai.events import (
    EventBus, PermissionRequested, PermissionResolved,
)


_ORCHESTRATOR = "orchestrator"


def _scope_label_always_allow(*, agent_name: str, tool_name: str) -> str:
    if agent_name == _ORCHESTRATOR:
        return f"Always allow {tool_name} for the orchestrator"
    return f"Always allow {tool_name} for any future agent named {agent_name!r}"


def _scope_label_always_deny(*, agent_name: str, tool_name: str) -> str:
    if agent_name == _ORCHESTRATOR:
        return f"Always deny {tool_name} for the orchestrator"
    return f"Always deny {tool_name} for any future agent named {agent_name!r}"


class PermissionModal(ModalScreen[None]):
    """Global permission-prompt modal.

    Subscribes to PermissionRequested. While at least one request is
    pending, displays the head of the queue with Allow/Deny buttons +
    explicit-scope variants. Resolves directly via the session's
    PermissionInbox; uses the bus to receive new requests and to broadcast
    PermissionResolved so the per-agent transcript bar can clear its
    inline view.
    """

    DEFAULT_CSS = """
    PermissionModal { align: center middle; }
    PermissionModal > Vertical {
        width: 90; height: auto; padding: 1 2;
        background: $surface; border: round $warning;
    }
    PermissionModal #title { text-style: bold; }
    PermissionModal #buttons { height: 3; align-horizontal: center; }
    PermissionModal Button { margin: 0 1; }
    """

    BINDINGS = [Binding("escape", "deny_once", "deny once")]

    def __init__(
        self,
        *,
        inbox_lookup: Callable[[str], PermissionInbox | None],
        grants: PermissionGrants,
    ) -> None:
        super().__init__()
        self._inbox_lookup = inbox_lookup
        self._grants = grants
        self._queue: list[PermissionRequested] = []
        self._current_request: PermissionRequested | None = None
        self._unsub_req = lambda: None
        self._unsub_res = lambda: None

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Permission requested", id="title")
            yield Label("(no pending request)", id="prompt")
            yield Label("", id="agent")
            yield Label("", id="tool-args")
            with Horizontal(id="buttons"):
                yield Button("Allow once", id="allow-once", variant="success")
                yield Button(
                    "Allow for the rest of this run",
                    id="allow-session", variant="success",
                )
                yield Button("Always allow", id="allow-always", variant="success")
                yield Button("Deny once", id="deny-once", variant="error")
                yield Button("Always deny", id="deny-always", variant="error")

    def on_mount(self) -> None:
        bus: EventBus = self.app.event_bus
        self._unsub_req = bus.subscribe(PermissionRequested, self._on_request)
        self._unsub_res = bus.subscribe(PermissionResolved, self._on_resolved_elsewhere)

    def on_unmount(self) -> None:
        self._unsub_req()
        self._unsub_res()

    def _on_request(self, event: PermissionRequested) -> None:
        if self._current_request is None:
            self._current_request = event
            self._render_current()
        else:
            self._queue.append(event)

    def _on_resolved_elsewhere(self, event: PermissionResolved) -> None:
        if (self._current_request is not None
                and self._current_request.request_id == event.request_id):
            self._advance()
            return
        self._queue = [q for q in self._queue if q.request_id != event.request_id]

    def _render_current(self) -> None:
        req = self._current_request
        if req is None:
            return
        self.query_one("#prompt", Label).update(
            req.title or f"Allow {req.tool_name}?"
        )
        agent_label = (
            "agent: orchestrator" if req.agent_name == _ORCHESTRATOR
            else f"agent: {req.agent_name}"
        )
        self.query_one("#agent", Label).update(agent_label)
        self.query_one("#tool-args", Label).update(
            f"{req.tool_name}({_short_repr(req.tool_input)})"
        )
        self.query_one("#allow-always", Button).label = _scope_label_always_allow(
            agent_name=req.agent_name, tool_name=req.tool_name,
        )
        self.query_one("#deny-always", Button).label = _scope_label_always_deny(
            agent_name=req.agent_name, tool_name=req.tool_name,
        )

    def _advance(self) -> None:
        if self._queue:
            self._current_request = self._queue.pop(0)
            self._render_current()
        else:
            self._current_request = None
            self.query_one("#prompt", Label).update("(no pending request)")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if self._current_request is None:
            return
        bid = event.button.id
        if bid == "allow-once":
            self._resolve("allow", scope=None)
        elif bid == "allow-session":
            self._resolve("allow", scope="session")
        elif bid == "allow-always":
            self._resolve("allow", scope="persistent")
        elif bid == "deny-once":
            self._resolve("deny", scope=None)
        elif bid == "deny-always":
            self._resolve("deny", scope="persistent")

    def action_deny_once(self) -> None:
        if self._current_request is None:
            self.dismiss(None)
            return
        self._resolve("deny", scope=None)

    def _resolve(self, behavior: str, *, scope: str | None) -> None:
        req = self._current_request
        if req is None:
            return
        if scope is not None:
            self._grants.remember(
                agent_name=req.agent_name, tool_name=req.tool_name,
                behavior=behavior, scope=scope,
            )
        result = (
            PermissionResultAllow() if behavior == "allow"
            else PermissionResultDeny(message="user denied")
        )
        inbox = self._inbox_lookup(req.agent_id)
        if inbox is not None:
            inbox.resolve(req.request_id, result)
        # Advance before publishing so _on_resolved_elsewhere skips this ID.
        self._advance()
        self.app.event_bus.publish(PermissionResolved(
            agent_id=req.agent_id, request_id=req.request_id,
            behavior=behavior,
        ))


def _short_repr(value: object, limit: int = 200) -> str:
    s = repr(value)
    return s if len(s) <= limit else s[: limit - 1] + "…"
