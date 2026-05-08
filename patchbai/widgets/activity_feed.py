from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container, Horizontal, VerticalScroll
from textual.widgets import Static

from patchbai.activity.log import ActivityEntry, ActivityKind
from patchbai.events import ActivityLogged

MODES: tuple[str, ...] = ("audit", "agents", "notifs", "debug")

# Per-mode kind allowlists, derived from the design spec table.
_MODE_KINDS: dict[str, frozenset[str]] = {
    "audit": frozenset({
        ActivityKind.AGENT_SPAWNED, ActivityKind.AGENT_STATE, ActivityKind.AGENT_DONE,
        ActivityKind.AGENT_ASK, ActivityKind.AGENT_NOTIFY, ActivityKind.AGENT_ARCHIVE,
        ActivityKind.ORCH_USER, ActivityKind.ORCH_REPLY, ActivityKind.ORCH_SESSION,
        ActivityKind.LAYOUT_APPLIED, ActivityKind.LAYOUT_FAILED,
        ActivityKind.TAB_ADDED, ActivityKind.TAB_CLOSED,
        ActivityKind.WORKSPACE_CWD,
    }),
    "agents": frozenset({
        ActivityKind.AGENT_SPAWNED, ActivityKind.AGENT_STATE, ActivityKind.AGENT_DONE,
        ActivityKind.AGENT_MESSAGE, ActivityKind.AGENT_ASK, ActivityKind.AGENT_NOTIFY,
        ActivityKind.AGENT_ARCHIVE,
    }),
    "notifs": frozenset({
        ActivityKind.AGENT_DONE, ActivityKind.AGENT_ASK, ActivityKind.AGENT_NOTIFY,
        ActivityKind.LAYOUT_FAILED, ActivityKind.WORKSPACE_CWD,
    }),
    "debug": frozenset({
        ActivityKind.AGENT_SPAWNED, ActivityKind.AGENT_STATE, ActivityKind.AGENT_DONE,
        ActivityKind.AGENT_MESSAGE, ActivityKind.AGENT_TOOL, ActivityKind.AGENT_ASK,
        ActivityKind.AGENT_NOTIFY, ActivityKind.AGENT_ARCHIVE,
        ActivityKind.ORCH_USER, ActivityKind.ORCH_REPLY, ActivityKind.ORCH_SESSION,
        ActivityKind.LAYOUT_APPLIED, ActivityKind.LAYOUT_FAILED,
        ActivityKind.TAB_ADDED, ActivityKind.TAB_CLOSED, ActivityKind.TAB_SWITCHED,
        ActivityKind.WORKSPACE_CWD, ActivityKind.FILE_SELECTED,
    }),
}


_VARIANT: dict[str, str] = {
    # Compact: routine signals.
    ActivityKind.TAB_ADDED: "compact",
    ActivityKind.TAB_CLOSED: "compact",
    ActivityKind.TAB_SWITCHED: "compact",
    ActivityKind.LAYOUT_APPLIED: "compact",
    ActivityKind.WORKSPACE_CWD: "compact",
    ActivityKind.AGENT_STATE: "compact",
    ActivityKind.AGENT_ARCHIVE: "compact",
    ActivityKind.FILE_SELECTED: "compact",
    ActivityKind.AGENT_TOOL: "compact",
    ActivityKind.ORCH_SESSION: "compact",

    # Expanded: carries a body worth reading.
    ActivityKind.ORCH_USER: "expanded",
    ActivityKind.ORCH_REPLY: "expanded",
    ActivityKind.AGENT_MESSAGE: "expanded",
    ActivityKind.AGENT_NOTIFY: "expanded",
    ActivityKind.AGENT_SPAWNED: "expanded",

    # Card: needs attention.
    ActivityKind.AGENT_ASK: "card",
    ActivityKind.LAYOUT_FAILED: "card",
    # AGENT_DONE: "compact" by default; ERROR overrides to "card" — handled by _variant_for.
    ActivityKind.AGENT_DONE: "compact",
}


def _variant_for(entry: ActivityEntry) -> str:
    """Pick the variant for an entry. Most kinds map statically via _VARIANT;
    agent.done escalates to 'card' when the underlying state is ERROR."""
    if entry.kind == ActivityKind.AGENT_DONE:
        from patchbai.events import AgentStateChanged
        from patchbai.agents.state import AgentState
        raw = entry.raw
        if isinstance(raw, AgentStateChanged) and raw.info.state == AgentState.ERROR:
            return "card"
        return "compact"
    return _VARIANT.get(entry.kind, "compact")


class _ModeChip(Static):
    """Clickable mode label inside the chip strip. Carries the mode string;
    parent ActivityFeed reads `event.widget.mode` on click."""

    DEFAULT_CSS = """
    _ModeChip {
        padding: 0 1;
        margin: 0 1 0 0;
        border: tall $surface-lighten-2;
        color: $text;
    }
    _ModeChip.-active {
        border: tall $primary;
        color: $primary;
    }
    _ModeChip:hover {
        background: $boost;
    }
    """

    def __init__(self, mode: str, *, active: bool) -> None:
        super().__init__(mode.capitalize())
        self.mode = mode
        if active:
            self.add_class("-active")


class _ModeChips(Horizontal):
    DEFAULT_CSS = """
    _ModeChips {
        height: auto;
        padding: 0 1;
        background: $boost;
    }
    """

    def __init__(self, active: str) -> None:
        super().__init__()
        self._active = active

    def compose(self) -> ComposeResult:
        for m in MODES:
            yield _ModeChip(m, active=(m == self._active))


class _ActivityRow(Static):
    """One feed row. Variant comes from `_variant_for(entry)`; CSS classes
    `-variant-compact|expanded|card` drive presentation."""

    DEFAULT_CSS = """
    _ActivityRow {
        height: auto;
        padding: 0 1;
    }
    _ActivityRow.-variant-card {
        border: round $warning;
        padding: 0 1;
        margin: 0 0 1 0;
    }
    """

    def __init__(self, entry: ActivityEntry) -> None:
        variant = _variant_for(entry)
        text = self._format(entry, variant)
        super().__init__(text)
        self.entry = entry
        # Plain attribute mirroring the rendered string. Static stores its
        # content in a private `_renderable` field that isn't part of the
        # public API; consumers (tests, click handlers) read this instead.
        self.text = text
        self.add_class(f"-variant-{variant}")

    @staticmethod
    def _format(entry: ActivityEntry, variant: str) -> str:
        ts = entry.timestamp.strftime("%H:%M:%S")
        head = f"[{ts}] {entry.kind:<18} {entry.summary}"
        if variant == "compact" or not entry.detail:
            return head
        if variant == "expanded":
            return f"{head}\n            ↳ {entry.detail}"
        # card
        return f"{entry.kind} · {entry.summary}\n{entry.detail}"


class ActivityFeed(Container):
    """Real Activity Feed. Reads backlog from `app.activity_log` on mount,
    subscribes to `ActivityLogged` for live updates, and renders rows whose
    `kind` is allowed by the current `mode`. Mode is selected via the `mode`
    prop (one of `MODES`); invalid values silently fall back to `"audit"`."""

    DEFAULT_BORDER_TITLE = "Activity"

    DEFAULT_CSS = """
    ActivityFeed {
        border: round $surface-lighten-2;
        padding: 0 1;
    }
    ActivityFeed VerticalScroll {
        height: 1fr;
    }
    """

    def __init__(self, *, mode: str | None = None) -> None:
        super().__init__()
        if mode is not None and mode not in _MODE_KINDS:
            mode = None  # silently fall back to default; no invariant break
        self.mode: str = mode or "audit"
        self._unsub = None

    def compose(self) -> ComposeResult:
        yield _ModeChips(active=self.mode)
        yield VerticalScroll(id="activity-rows")

    def on_mount(self) -> None:
        # Tolerate test contexts where the app fixture skipped wiring
        # event_bus / activity_log (mirrors AgentTable's defensive pattern).
        bus = getattr(self.app, "event_bus", None)
        log = getattr(self.app, "activity_log", None)
        if bus is None or log is None:
            return
        scroll = self.query_one("#activity-rows", VerticalScroll)
        allow = _MODE_KINDS[self.mode]
        for entry in log.entries():
            if entry.kind in allow:
                scroll.mount(_ActivityRow(entry))
        self._unsub = bus.subscribe(ActivityLogged, self._on_logged)

    def on_unmount(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None

    def _on_logged(self, event: ActivityLogged) -> None:
        entry: ActivityEntry = event.entry  # type: ignore[assignment]
        if entry.kind not in _MODE_KINDS[self.mode]:
            return
        scroll = self.query_one("#activity-rows", VerticalScroll)
        scroll.mount(_ActivityRow(entry))

    def on_click(self, event) -> None:
        # Identify whether the click landed on a _ModeChip and switch.
        target = event.widget if hasattr(event, "widget") else None
        if not isinstance(target, _ModeChip):
            return
        new_mode = target.mode
        if new_mode == self.mode:
            return
        self._set_mode(new_mode)
        event.stop()

    def _set_mode(self, new_mode: str) -> None:
        self.mode = new_mode
        # Update chip styling.
        for chip in self.query(_ModeChip):
            chip.set_class(chip.mode == new_mode, "-active")
        # Rebuild the scroll region for the new mode.
        scroll = self.query_one("#activity-rows", VerticalScroll)
        scroll.remove_children()
        log = getattr(self.app, "activity_log", None)
        if log is not None:
            allow = _MODE_KINDS[new_mode]
            for entry in log.entries():
                if entry.kind in allow:
                    scroll.mount(_ActivityRow(entry))
        # Persist the new mode into the layout JSON for this panel.
        self._persist_mode(new_mode)

    def _persist_mode(self, new_mode: str) -> None:
        """Walk the active tab's layout dict, find this widget's panel entry
        by id (panel-{node.id} → node.id == self.id minus prefix), update its
        `props.mode`, and call app._apply_to_tab to validate + save."""
        app = self.app
        active_tab_id = getattr(app, "_active_tab_id", None)
        ws = getattr(app, "_workspace", None)
        if active_tab_id is None or ws is None:
            return
        # The widget id is "panel-{node.id}". Extract the node id.
        if not self.id or not self.id.startswith("panel-"):
            return
        node_id = self.id[len("panel-"):]
        # Find the active tab's spec, deep-copy it, mutate the matching panel.
        from patchbai.layout.spec import LayoutSpec
        target_tab = next((t for t in ws.tabs if t.id == active_tab_id), None)
        if target_tab is None:
            return
        spec_dict = target_tab.layout.model_dump(mode="json")

        def _walk(node: dict) -> bool:
            if node.get("widget") == "ActivityFeed" and node.get("id") == node_id:
                node.setdefault("props", {})["mode"] = new_mode
                return True
            for child in node.get("children", []) or []:
                if _walk(child):
                    return True
            return False

        if not _walk(spec_dict["layout"]):
            return
        try:
            new_spec = LayoutSpec.model_validate(spec_dict)
        except Exception:
            return
        import asyncio as _asyncio
        _asyncio.create_task(app._apply_to_tab(active_tab_id, new_spec))
