import json
from typing import Any

from mod_tui.layout.spec import LayoutSpec


def _ok(payload: dict) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(payload, indent=2)}]}


def _err(message: str, **extra: Any) -> dict:
    body = {"error": message}
    body.update(extra)
    return {"content": [{"type": "text", "text": json.dumps(body, indent=2)}]}


def add_tab_handler(app):
    """Build an MCP handler that delegates to app.add_tab.

    args:
        title: tab strip label (required).
        layout: optional. Either a LayoutSpec dict, or a string naming a
                saved layout in NamedLayoutsStore. If None, a default seed
                is used (chat-only if the workspace has no chat yet, else
                ActivityFeed-only).
        activate: bool, default True.
    """
    async def add_tab_tool(args: dict) -> dict:
        title = args.get("title")
        if not title or not isinstance(title, str):
            return _err("`title` is required and must be a string")
        raw_layout = args.get("layout")
        activate = bool(args.get("activate", True))
        try:
            if raw_layout is None:
                layout = app._default_seed_layout()
            elif isinstance(raw_layout, str):
                spec = app.layouts_store.load(raw_layout)
                if spec is None:
                    return _err(f"named layout not found: {raw_layout}",
                                suggestion="call list_layouts to see available names")
                layout = spec
            elif isinstance(raw_layout, dict):
                layout = LayoutSpec.model_validate(raw_layout)
            else:
                return _err("`layout` must be a dict, a string name, or omitted")
        except Exception as e:
            return _err(f"invalid layout: {e}")
        try:
            tab_id = await app.add_tab(title, layout, activate=activate)
        except Exception as e:
            return _err(f"add_tab failed: {e}")
        return _ok({"tab_id": tab_id, "title": title, "active": activate})

    return add_tab_tool


def close_tab_handler(app):
    async def close_tab_tool(args: dict) -> dict:
        tab_id = args.get("tab_id")
        if not isinstance(tab_id, str) or not tab_id:
            return _err("`tab_id` is required and must be a string")
        result = await app.close_tab(tab_id)
        if "error" in result:
            return _err(result["error"], **{k: v for k, v in result.items() if k != "error"})
        return _ok(result)

    return close_tab_tool


def switch_tab_handler(app):
    """Stub — implemented in Task 12."""
    raise NotImplementedError


def list_tabs_handler(app):
    """Stub — implemented in Task 13."""
    raise NotImplementedError
