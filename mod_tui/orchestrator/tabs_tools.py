import json
from typing import Any

from mod_tui.layout.spec import LayoutSpec


def _ok(payload: dict | list) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(payload, indent=2)}]}


def _err(message: str, **extra: Any) -> dict:
    body = {"error": message}
    body.update(extra)
    return {"content": [{"type": "text", "text": json.dumps(body, indent=2)}]}


def _panel_ids(node) -> list[str]:
    from mod_tui.layout.spec import Container, Panel, Tabs
    if isinstance(node, Panel):
        return [node.id]
    if isinstance(node, Tabs):
        return [c.id for c in node.children]
    if isinstance(node, Container):
        out: list[str] = []
        for c in node.children:
            out.extend(_panel_ids(c))
        return out
    return []


def _has_chat(node) -> bool:
    from mod_tui.workspace.spec import _contains_chat
    return _contains_chat(node)


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
    async def switch_tab_tool(args: dict) -> dict:
        tab_id = args.get("tab_id")
        if not isinstance(tab_id, str) or not tab_id:
            return _err("`tab_id` is required and must be a string")
        if app._workspace is None or all(t.id != tab_id for t in app._workspace.tabs):
            return _err("unknown_tab_id")
        from textual.widgets import TabbedContent
        tc = app.query_one("#app-tabs", TabbedContent)
        tc.active = f"tab-{tab_id}"
        return _ok({"active": tab_id})

    return switch_tab_tool


def list_tabs_handler(app):
    async def list_tabs_tool(_args: dict) -> dict:
        if app._workspace is None:
            return _ok([])
        out = []
        for t in app._workspace.tabs:
            out.append({
                "id": t.id,
                "title": t.title,
                "active": (t.id == app._active_tab_id),
                "has_chat": _has_chat(t.layout.layout),
                "panel_ids": _panel_ids(t.layout.layout),
            })
        return _ok(out)

    return list_tabs_tool
