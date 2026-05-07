from typing import Any

from patchbai.layout.spec import Panel


def resolve_title(panel: "Panel | dict[str, Any]", widget_cls: type) -> str:
    """Resolve the effective border title for a panel.

    Resolution order:
      1. ``panel.title`` if explicitly set.
      2. ``widget_cls.default_border_title(props)`` classmethod if defined.
      3. ``widget_cls.DEFAULT_BORDER_TITLE`` class attribute if defined.
      4. ``widget_cls.__name__`` as a last-resort fallback.

    Any exception raised inside ``default_border_title`` is swallowed and the
    resolution falls through to step 3 / step 4. A bad widget must never abort
    layout apply. Non-string returns from ``default_border_title`` or
    ``DEFAULT_BORDER_TITLE`` are also ignored — the resolver only emits ``str``.

    ``panel`` may be either a ``Panel`` model or a plain dict from
    ``model_dump`` (so ``get_layout`` can call this on a dumped tree without
    re-validating).
    """
    if isinstance(panel, Panel):
        explicit = panel.title
        props: dict[str, Any] = panel.props or {}
    else:
        explicit = panel.get("title")
        props = panel.get("props") or {}

    if isinstance(explicit, str) and explicit:
        return explicit

    fn = getattr(widget_cls, "default_border_title", None)
    if callable(fn):
        try:
            value = fn(props)
        except Exception:
            value = None
        if isinstance(value, str) and value:
            return value

    static = getattr(widget_cls, "DEFAULT_BORDER_TITLE", None)
    if isinstance(static, str) and static:
        return static

    return widget_cls.__name__


def populate_effective_titles(node: Any, registry) -> None:
    """Walk a dumped LayoutSpec tree and fill in each panel's effective title.

    Operates in-place on the dict returned by ``LayoutSpec.model_dump(mode='json')``.
    Used by the ``get_layout`` MCP tool so the orchestrator sees the same
    titles the user sees.
    """
    if not isinstance(node, dict):
        return
    if "widget" in node:
        # Leaf panel.
        if not node.get("title"):
            try:
                cls = registry.get(node["widget"])
            except Exception:
                node["title"] = node["widget"]
                return
            node["title"] = resolve_title(node, cls)
        return
    for child in node.get("children", []):
        populate_effective_titles(child, registry)
