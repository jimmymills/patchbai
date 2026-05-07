import weakref
from dataclasses import dataclass

from mod_tui.events import LayoutApplied, LayoutFailed
from mod_tui.layout.spec import Container, LayoutSpec, Panel, Tabs
from mod_tui.layout.titles import resolve_title


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
    elif isinstance(node, Tabs):
        for c in node.children:
            out[c.id] = c
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
from textual.widget import Widget
from textual.widgets import TabbedContent, TabPane

from mod_tui.layout.splitter import Splitter


def _has_border_in_default_css(cls) -> bool:
    """Heuristic: does this class (or an ancestor) declare a border in
    DEFAULT_CSS? Used by the safety net so we don't clobber a widget's own
    border style. Walks the MRO so subclasses inherit the answer.

    Naive substring match — will false-positive on `border:` appearing in
    a CSS comment or in a selector name like `.border-panel`. The failure
    mode (skipping the safety net when we shouldn't) is cosmetic, not
    functional, and rare enough to accept here."""
    for base in cls.__mro__:
        css = getattr(base, "DEFAULT_CSS", "") or ""
        if isinstance(css, str) and ("border:" in css or "border-" in css):
            return True
    return False


def _build(node, registry) -> Widget:
    if isinstance(node, Panel):
        cls = registry.get(node.widget)
        widget = cls(**node.props) if node.props else cls()
        widget.id = f"panel-{node.id}"
        widget.can_focus = True  # panels must be focusable so focus survives rebuilds
        if node.size:
            widget.styles.width = node.size if "%" in node.size or node.size.endswith("fr") else None
            widget.styles.height = None
        # Border safety net: widgets with no DEFAULT_CSS border get a default
        # one so border_title renders. Widgets with their own border keep it.
        # CSS variables (e.g. $surface-lighten-2) are not valid as inline style
        # color values before mount — use a concrete grey that approximates the
        # default dark-mode surface colour.
        if not _has_border_in_default_css(cls):
            widget.styles.border = ("round", "#3a3a3a")
        # Title resolution (never aborts the apply on a buggy widget).
        try:
            widget.border_title = resolve_title(node, cls)
        except Exception:
            widget.border_title = cls.__name__
        return widget
    if isinstance(node, Tabs):
        panes = []
        for child in node.children:
            inner = _build(child, registry)            # reuses Panel branch
            label = child.title or _default_pane_label(child, registry)
            panes.append(TabPane(label, inner, id=f"tabpane-{child.id}"))
        initial_id = f"tabpane-{node.active or node.children[0].id}"
        # TabbedContent.__init__ takes *titles as strings; passing TabPane
        # objects there triggers render_str and fails pre-mount. Instead,
        # construct with no positional args and load panes via _tab_content
        # (the same slot that compose_add_child fills when using the context
        # manager syntax), then set _initial for the active tab.
        tc = TabbedContent(initial=initial_id)
        tc._tab_content = list(panes)
        if node.size:
            tc.styles.width = node.size
        return tc
    # Container
    box_cls = Horizontal if node.type == "horizontal" else Vertical
    built = [_build(c, registry) for c in node.children]
    # Interleave a draggable Splitter between each pair of siblings so the user
    # can resize panels with the mouse. Single-child containers get no splitter.
    interleaved: list[Widget] = []
    for i, child in enumerate(built):
        if i > 0:
            interleaved.append(Splitter(node.type))
        interleaved.append(child)
    box = box_cls(*interleaved)
    if node.size:
        box.styles.width = node.size
    return box


def _default_pane_label(panel: Panel, registry) -> str:
    """Best-effort label for a TabPane when the panel has no explicit title.
    Reuses resolve_title against the widget class so widgets that publish a
    DEFAULT_BORDER_TITLE feed the tab label too."""
    try:
        cls = registry.get(panel.widget)
        return resolve_title(panel, cls)
    except Exception:
        return panel.widget


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
