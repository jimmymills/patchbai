from dataclasses import dataclass

from mod_tui.layout.spec import Container, LayoutSpec, Panel


# --- Operations -------------------------------------------------------------

@dataclass(frozen=True)
class MountPanel:
    panel: Panel


@dataclass(frozen=True)
class UnmountPanel:
    panel_id: str


@dataclass(frozen=True)
class UpdateProps:
    panel_id: str
    props: dict


Operation = MountPanel | UnmountPanel | UpdateProps


# --- Diff -------------------------------------------------------------------

def _collect_panels(node, out: dict[str, Panel]) -> None:
    if isinstance(node, Panel):
        out[node.id] = node
    elif isinstance(node, Container):
        for c in node.children:
            _collect_panels(c, out)


def diff(old: LayoutSpec | None, new: LayoutSpec) -> list[Operation]:
    """Compute the minimal set of mount/unmount/update operations to take the
    rendered widget tree from `old` to `new`.

    Note: this plan reuses widgets only when the panel id AND widget type are
    unchanged. Container restructuring is handled by Task 14's apply step,
    which rebuilds the container scaffolding from `new.layout` each call.
    Reusing identical panels means no scroll-jump or focus-loss for the cases
    that matter most (props-only changes)."""

    old_panels: dict[str, Panel] = {}
    new_panels: dict[str, Panel] = {}
    if old is not None:
        _collect_panels(old.layout, old_panels)
    _collect_panels(new.layout, new_panels)

    ops: list[Operation] = []

    for pid, op in old_panels.items():
        if pid not in new_panels or new_panels[pid].widget != op.widget:
            ops.append(UnmountPanel(panel_id=pid))

    for pid, np in new_panels.items():
        if pid not in old_panels or old_panels[pid].widget != np.widget:
            ops.append(MountPanel(panel=np))
            continue
        if old_panels[pid].props != np.props:
            ops.append(UpdateProps(panel_id=pid, props=np.props))

    return ops


# --- Apply ------------------------------------------------------------------

from textual.containers import Container as TxContainer
from textual.containers import Horizontal, Vertical


def _build(node, registry) -> "TxContainer":
    if isinstance(node, Panel):
        cls = registry.get(node.widget)
        widget = cls(**node.props) if node.props else cls()
        widget.id = f"panel-{node.id}"
        widget.can_focus = True  # panels must be focusable so focus survives rebuilds
        if node.size:
            widget.styles.width = node.size if "%" in node.size or node.size.endswith("fr") else None
            widget.styles.height = None
        return widget
    box_cls = Horizontal if node.type == "horizontal" else Vertical
    box = box_cls(*[_build(c, registry) for c in node.children])
    if node.size:
        box.styles.width = node.size
    return box


async def apply(container: TxContainer, spec: LayoutSpec, registry) -> None:
    """Replace `container`'s children with widgets built from `spec.layout`.

    Preserves focus across rebuilds: if no explicit `spec.focus` is set, the
    currently-focused panel id (if any) is restored after the rebuild —
    provided the panel still exists in the new spec.

    Atomic: builds the new tree fully (registry lookups + instantiation)
    before touching the container. If anything raises, nothing is mounted.
    """
    new_children = [_build(spec.layout, registry)]

    # Snapshot the currently-focused panel id (e.g., "orch" from "panel-orch").
    snapshot_focus_id: str | None = None
    try:
        app = container.app
        focused = app.focused
        if focused is not None and focused.id and focused.id.startswith("panel-"):
            snapshot_focus_id = focused.id[len("panel-"):]
    except Exception:
        pass

    await container.remove_children()
    await container.mount_all(new_children)

    target = spec.focus or snapshot_focus_id
    if target:
        try:
            container.query_one(f"#panel-{target}").focus()
        except Exception:
            pass
