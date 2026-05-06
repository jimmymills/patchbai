import weakref
from dataclasses import dataclass

from mod_tui.events import LayoutApplied, LayoutFailed
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


# Track the most recent applied spec per container, keyed by the container
# instance itself. WeakKeyDictionary ensures stale entries gc out when the
# container is no longer referenced — important since `apply` is called many
# times per session in tests, and id-keyed caches are footguns once the
# garbage collector reuses ids.
_last_applied_spec: "weakref.WeakKeyDictionary[TxContainer, LayoutSpec]" = weakref.WeakKeyDictionary()


async def apply(container: TxContainer, spec: LayoutSpec, registry,
                *, layout_name: str | None = None) -> None:
    """Replace `container`'s children with widgets built from `spec.layout`.

    Behavior:
    - **Idempotent fast-path:** if `spec` equals the last-applied spec for this
      container, skip the rebuild entirely. A `LayoutApplied` event is still
      fired so subscribers can refresh anything spec-derived (e.g., the
      StatusBar's layout name).
    - **Atomic build:** the new widget tree is fully constructed before any
      existing child is removed. If `_build` raises (e.g., UnknownWidgetError),
      a `LayoutFailed(error=str(exc))` event is published and the previous
      mounted layout stays untouched. The exception is re-raised.
    - **Focus preservation:** if `spec.focus` is None and a panel is currently
      focused, restore that panel's id after the rebuild (provided the panel
      survives in the new spec).
    """
    bus = getattr(container.app, "event_bus", None)

    # Fast-path: same spec → no rebuild.
    if _last_applied_spec.get(container) == spec:
        if bus is not None:
            bus.publish(LayoutApplied(spec=spec, layout_name=layout_name))
        return

    # Atomic build (raises before any mount changes).
    try:
        new_children = [_build(spec.layout, registry)]
    except Exception as e:
        if bus is not None:
            bus.publish(LayoutFailed(error=str(e)))
        raise

    # Snapshot focus.
    snapshot_focus_id: str | None = None
    try:
        focused = container.app.focused
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

    _last_applied_spec[container] = spec
    if bus is not None:
        bus.publish(LayoutApplied(spec=spec, layout_name=layout_name))
