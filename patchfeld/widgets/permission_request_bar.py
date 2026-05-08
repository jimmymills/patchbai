from typing import Literal, TYPE_CHECKING

if TYPE_CHECKING:
    from patchfeld.agents.permission_grants import PermissionGrants

from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Button, Static

from patchfeld.events import PermissionRequested, PermissionResolved


class PermissionRequestBar(Horizontal):
    """A single inline approval row mounted at the top of an AgentTranscript
    while a permission request is pending for that agent.

    Clicks call into the agent's PermissionInbox via the App's manager;
    coordination with the global modal happens through PermissionResolved.
    """

    DEFAULT_CSS = """
    PermissionRequestBar {
        height: 3;
        background: $warning-darken-2;
        padding: 0 1;
    }
    PermissionRequestBar Static.label { width: 1fr; }
    PermissionRequestBar Button { margin: 0 1; }
    """

    def __init__(self, *, request: PermissionRequested) -> None:
        super().__init__()
        self._request = request

    @property
    def request_id(self) -> str:
        return self._request.request_id

    def compose(self) -> ComposeResult:
        req = self._request
        title = req.title or f"Allow {req.tool_name}?"
        yield Static(
            f"⚠ {title} — {req.tool_name}({_short(req.tool_input)})",
            classes="label",
        )
        yield Button("Allow", id="bar-allow-once", variant="success")
        yield Button("Always allow", id="bar-allow-always", variant="success")
        yield Button("Deny", id="bar-deny-once", variant="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        grants = getattr(self.app, "_permission_grants", None)
        if bid == "bar-allow-once":
            self._resolve("allow", scope=None, grants=grants)
        elif bid == "bar-allow-always":
            self._resolve("allow", scope="persistent", grants=grants)
        elif bid == "bar-deny-once":
            self._resolve("deny", scope=None, grants=grants)

    def _resolve(
        self,
        behavior: Literal["allow", "deny"],
        *,
        scope: Literal["persistent", "session"] | None,
        grants: "PermissionGrants | None",
    ) -> None:
        req = self._request
        if scope is not None and grants is not None:
            grants.remember(
                agent_name=req.agent_name, tool_name=req.tool_name,
                behavior=behavior, scope=scope,
            )
        manager = getattr(self.app, "manager", None)
        inbox = manager.get_permission_inbox(req.agent_id) if manager else None
        if inbox is not None:
            result = (
                PermissionResultAllow() if behavior == "allow"
                else PermissionResultDeny(message="user denied")
            )
            inbox.resolve(req.request_id, result)
        self.app.event_bus.publish(PermissionResolved(
            agent_id=req.agent_id, request_id=req.request_id,
            behavior=behavior,
        ))


def _short(value: object, limit: int = 80) -> str:
    s = repr(value)
    return s if len(s) <= limit else s[: limit - 1] + "…"
