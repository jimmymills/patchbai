from __future__ import annotations

from textual import events
from textual.widget import Widget


class Splitter(Widget):
    """Draggable 1-cell bar between sibling widgets in a Horizontal/Vertical box.

    On mouse drag, mutates the previous and next siblings' inline
    `styles.width`/`styles.height` to fixed cell counts. Sizes are ephemeral —
    LayoutEngine.apply replaces the whole widget tree on layout reapply, so
    drags do not survive a layout reload.
    """

    DEFAULT_CSS = """
    Splitter {
        background: $panel;
        color: $foreground 30%;
    }
    Splitter.-vertical {
        width: 1;
        height: 1fr;
    }
    Splitter.-horizontal {
        width: 1fr;
        height: 1;
    }
    Splitter:hover, Splitter.-dragging {
        background: $accent;
        color: $accent;
    }
    """

    can_focus = False

    def __init__(self, container_orientation: str) -> None:
        super().__init__()
        self._container_orientation = container_orientation
        self.add_class("-vertical" if container_orientation == "horizontal" else "-horizontal")
        self._drag_start: tuple[int, int] | None = None
        self._initial_prev: int = 0
        self._initial_next: int = 0

    def render(self) -> str:
        return "│" if self._container_orientation == "horizontal" else "─"

    def on_mouse_down(self, event: events.MouseDown) -> None:
        prev_sib, next_sib = self._neighbors()
        if prev_sib is None or next_sib is None:
            return
        if self._container_orientation == "horizontal":
            self._initial_prev = prev_sib.size.width
            self._initial_next = next_sib.size.width
        else:
            self._initial_prev = prev_sib.size.height
            self._initial_next = next_sib.size.height
        self._drag_start = (event.screen_x, event.screen_y)
        self.add_class("-dragging")
        self.capture_mouse()
        event.stop()

    def on_mouse_move(self, event: events.MouseMove) -> None:
        if self._drag_start is None:
            return
        prev_sib, next_sib = self._neighbors()
        if prev_sib is None or next_sib is None:
            return
        sx, sy = self._drag_start
        if self._container_orientation == "horizontal":
            delta = event.screen_x - sx
            new_prev = max(1, self._initial_prev + delta)
            new_next = max(1, self._initial_next - delta)
            prev_sib.styles.width = new_prev
            next_sib.styles.width = new_next
        else:
            delta = event.screen_y - sy
            new_prev = max(1, self._initial_prev + delta)
            new_next = max(1, self._initial_next - delta)
            prev_sib.styles.height = new_prev
            next_sib.styles.height = new_next
        event.stop()

    def on_mouse_up(self, event: events.MouseUp) -> None:
        if self._drag_start is not None:
            self._drag_start = None
            self.remove_class("-dragging")
            self.release_mouse()
            event.stop()

    def _neighbors(self) -> tuple[Widget | None, Widget | None]:
        parent = self.parent
        if parent is None:
            return (None, None)
        siblings = list(parent.children)
        try:
            idx = siblings.index(self)
        except ValueError:
            return (None, None)
        prev = siblings[idx - 1] if idx > 0 else None
        nxt = siblings[idx + 1] if idx + 1 < len(siblings) else None
        return (prev, nxt)
