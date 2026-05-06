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

    Atomic: builds the new tree fully (including registry lookups for every
    panel widget class) before touching the container. If any lookup raises
    UnknownWidgetError or instantiation throws, nothing is mounted.

    `focus` is honored after mount.

    Note for plan 1: `apply` rebuilds from scratch on every call. The `diff`
    function above is fully implemented and tested because its semantics are
    the stable contract — but `apply` only ever runs once per app lifetime in
    this plan (mounting the default dashboard at boot), so a diff-driven
    incremental application would be premature. Plan 4 (when `set_layout`
    becomes a runtime tool the orchestrator can call repeatedly) will switch
    `apply` to consume `diff()` operations and reuse mounted widgets where ids
    and widget types match.
    """
    new_children = [_build(spec.layout, registry)]

    await container.remove_children()
    await container.mount_all(new_children)

    if spec.focus:
        try:
            container.query_one(f"#panel-{spec.focus}").focus()
        except Exception:
            pass
