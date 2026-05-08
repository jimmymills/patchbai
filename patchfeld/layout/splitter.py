from __future__ import annotations

from textual import events
from textual.containers import Container as TxContainer
from textual.widget import Widget

from patchfeld.events import LayoutResized


class Splitter(Widget):
    """Draggable 1-cell bar between sibling widgets in a Horizontal/Vertical box.

    On mouse drag, mutates the previous and next siblings' inline
    `styles.width`/`styles.height` to fixed cell counts (visual feedback).
    On mouse-up, emits a LayoutResized event with the final sizes converted
    to percentages of the parent container's inner extent so the app can
    persist them to the workspace.
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

    def __init__(
        self,
        container_orientation: str,
        *,
        parent_path: tuple[int, ...] = (),
        prev_index: int = 0,
        next_index: int = 1,
    ) -> None:
        super().__init__()
        self._container_orientation = container_orientation
        self._parent_path = parent_path
        self._prev_index = prev_index
        self._next_index = next_index
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
        if self._drag_start is None:
            return
        sx, sy = self._drag_start
        moved = (event.screen_x, event.screen_y) != (sx, sy)
        self._drag_start = None
        self.remove_class("-dragging")
        self.release_mouse()
        event.stop()
        # A plain click (mouse_down + mouse_up at same coords) shouldn't
        # rewrite the spec — only publish on a real drag.
        if moved:
            self._publish_resize()

    def _publish_resize(self) -> None:
        """Snapshot the parent's post-drag layout and emit LayoutResized.

        Uses each non-splitter child's `outer_size` (full footprint including
        any border) so the app handler can convert to percentages that sum to
        exactly 100% — the previous version compared inner widths against a
        denominator that included the splitter and child borders, so every
        save lost ~3% of the layout to drift."""
        parent = self.parent
        if not isinstance(parent, Widget):
            return
        cells: list[int] = []
        for child in parent.children:
            if isinstance(child, Splitter):
                continue
            if self._container_orientation == "horizontal":
                cells.append(child.outer_size.width)
            else:
                cells.append(child.outer_size.height)
        if not cells or sum(cells) <= 0:
            return

        tab_id = self._owning_tab_id()
        if tab_id is None:
            return
        bus = getattr(self.app, "event_bus", None)
        if bus is None:
            return

        bus.publish(LayoutResized(
            tab_id=tab_id,
            parent_path=self._parent_path,
            children_cells=tuple(cells),
        ))

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

    def _owning_tab_id(self) -> str | None:
        """Walk up ancestors until we find the per-tab `panel-area-{tab_id}`
        container. Returns None if mounted outside an app tab (e.g., tests
        that mount Splitter under a generic Container)."""
        node = self.parent
        while node is not None:
            wid = getattr(node, "id", None)
            if isinstance(node, TxContainer) and wid and wid.startswith("panel-area-"):
                return wid[len("panel-area-"):]
            node = node.parent
        return None
