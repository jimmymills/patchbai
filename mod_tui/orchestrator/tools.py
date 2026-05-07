import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from claude_agent_sdk import create_sdk_mcp_server, tool

from mod_tui.actions import ActionRegistry
from mod_tui.agents.manager import AgentManager
from mod_tui.config import ConfigStore, KeyBinding
from mod_tui.layout.registry import WidgetRegistry
from mod_tui.layout.spec import LayoutSpec
from mod_tui.orchestrator.tabs_tools import (
    add_tab_handler,
    close_tab_handler,
    list_tabs_handler,
    rename_tab_handler,
    reorder_tabs_handler,
    switch_tab_handler,
)
from mod_tui.persistence.layouts_store import NamedLayoutsStore
from mod_tui.persistence.themes_store import NamedThemesStore
from mod_tui.persistence.workspace_store import save_workspace
from mod_tui.theme.engine import _EXTRA_CSS_KEY, apply_theme, palette_from_textual_theme
from mod_tui.theme.spec import ThemeSpec


@dataclass(frozen=True)
class _ToolSpec:
    name: str
    description: str
    input_schema: dict
    # build(manager) returns the async handler for this tool
    build: Callable[[AgentManager], Callable[[dict], Awaitable[dict]]]


def _spawn_handler(manager: AgentManager):
    async def spawn_agent(args: dict) -> dict:
        agent_id = await manager.spawn(
            name=args["name"],
            prompt=args["prompt"],
            cwd=args.get("cwd"),
            allowed_tools=args.get("allowed_tools"),
        )
        return {
            "content": [
                {"type": "text", "text": f"Spawned agent {agent_id} ({args['name']})"}
            ]
        }
    return spawn_agent


def _list_handler(manager: AgentManager):
    async def list_agents(_args: dict) -> dict:
        infos = [info.to_dict() for info in manager.list_infos()]
        return {"content": [{"type": "text", "text": json.dumps(infos, indent=2)}]}
    return list_agents


def _read_handler(manager: AgentManager):
    async def read_agent_transcript(args: dict) -> dict:
        entries = manager.read_transcript(args["agent_id"])
        text = "\n".join(f"[{e.role}] {e.text}" for e in entries)
        return {"content": [{"type": "text", "text": text}]}
    return read_agent_transcript


def _send_handler(manager: AgentManager):
    async def send_to_agent(args: dict) -> dict:
        agent_id = args["agent_id"]
        message = args["message"]
        try:
            await manager.send(agent_id, message)
            return {
                "content": [
                    {"type": "text", "text": f"Sent to {agent_id}: {message[:60]}"}
                ]
            }
        except KeyError:
            return {"content": [{"type": "text", "text": f"Unknown agent_id: {agent_id}"}]}
    return send_to_agent


def _interrupt_handler(manager: AgentManager):
    async def interrupt_agent(args: dict) -> dict:
        agent_id = args["agent_id"]
        if manager.get_session(agent_id) is None:
            return {"content": [{"type": "text", "text": f"Unknown agent_id: {agent_id}"}]}
        await manager.interrupt(agent_id)
        return {"content": [{"type": "text", "text": f"Sent interrupt to {agent_id}."}]}
    return interrupt_agent


def _kill_handler(manager: AgentManager):
    async def kill_agent(args: dict) -> dict:
        agent_id = args["agent_id"]
        if manager.get_session(agent_id) is None:
            return {"content": [{"type": "text", "text": f"Unknown agent_id: {agent_id}"}]}
        await manager.kill(agent_id)
        return {"content": [{"type": "text", "text": f"Killed agent {agent_id}."}]}
    return kill_agent


def _respond_handler(manager: AgentManager):
    async def respond_to_agent_request(args: dict) -> dict:
        agent_id = args["agent_id"]
        request_id = args["request_id"]
        response = args["response"]
        inbox = manager.get_inbox(agent_id)
        if inbox is None:
            return {
                "content": [{"type": "text", "text": f"Unknown agent_id (no inbox): {agent_id}"}]
            }
        inbox.resolve(request_id, response)
        return {
            "content": [
                {"type": "text", "text": f"Resolved request {request_id} for {agent_id}."}
            ]
        }
    return respond_to_agent_request


def _set_layout_handler(apply_layout, widget_registry=None):
    from mod_tui.layout.custom_widgets import register_custom_widget, CustomWidgetError

    async def set_layout_tool(args: dict) -> dict:
        try:
            spec = LayoutSpec.model_validate(args["spec"])
        except Exception as e:
            return {"content": [{"type": "text", "text": f"Invalid LayoutSpec: {e}"}]}
        # Register custom widgets BEFORE applying. If any source fails to
        # exec or doesn't yield a Widget subclass, abort the apply atomically.
        if spec.custom_widgets and widget_registry is not None:
            for cw in spec.custom_widgets:
                try:
                    register_custom_widget(widget_registry, cw.name, cw.source)
                except CustomWidgetError as e:
                    return {
                        "content": [{
                            "type": "text",
                            "text": f"Custom widget {cw.name!r} error: {e}",
                        }]
                    }
        try:
            await apply_layout(spec, tab_id=args.get("tab_id"))
        except Exception as e:
            return {"content": [{"type": "text", "text": f"Apply error: {e}"}]}
        return {"content": [{"type": "text", "text": "Layout applied."}]}
    return set_layout_tool


def _save_layout_handler(layouts_store: NamedLayoutsStore, app=None):
    async def save_layout_tool(args: dict) -> dict:
        name = args["name"]
        if "spec" in args:
            try:
                spec = LayoutSpec.model_validate(args["spec"])
            except Exception as e:
                return {"content": [{"type": "text", "text": f"Invalid LayoutSpec: {e}"}]}
        elif app is not None:
            tid = args.get("tab_id") or app._active_tab_id
            ws = app._workspace
            if ws is None or tid is None:
                return {"content": [{"type": "text", "text": "No tab to save."}]}
            tab = next((t for t in ws.tabs if t.id == tid), None)
            if tab is None:
                return {"content": [{"type": "text", "text": f"Unknown tab_id: {tid}"}]}
            spec = tab.layout
        else:
            return {"content": [{"type": "text", "text": "Provide `spec` or call from an app."}]}
        try:
            layouts_store.save(name, spec)
        except ValueError as e:
            return {"content": [{"type": "text", "text": f"Invalid layout name: {e}"}]}
        return {"content": [{"type": "text", "text": f"Saved layout {name!r}."}]}
    return save_layout_tool


def _load_layout_handler(apply_layout, layouts_store: NamedLayoutsStore, app=None):
    async def load_layout_tool(args: dict) -> dict:
        name = args["name"]
        spec = layouts_store.load(name)
        if spec is None:
            return {"content": [{"type": "text", "text": f"Layout not found: {name}"}]}
        as_new_tab = bool(args.get("as_new_tab"))
        if as_new_tab and app is not None:
            try:
                tab_id = await app.add_tab(args.get("title", name), spec, activate=True)
            except Exception as e:
                return {"content": [{"type": "text", "text": f"add_tab failed: {e}"}]}
            return {"content": [{"type": "text",
                                 "text": f"Loaded {name!r} into new tab {tab_id}."}]}
        try:
            await apply_layout(spec, layout_name=name, tab_id=args.get("tab_id"))
        except Exception as e:
            return {"content": [{"type": "text", "text": f"Apply error: {e}"}]}
        return {"content": [{"type": "text", "text": f"Loaded layout {name!r}."}]}
    return load_layout_tool


def _list_layouts_handler(layouts_store: NamedLayoutsStore):
    async def list_layouts_tool(_args: dict) -> dict:
        names = layouts_store.list()
        text = json.dumps(names)
        return {"content": [{"type": "text", "text": text}]}
    return list_layouts_tool


def _bind_key_handler(config_store: ConfigStore, actions: ActionRegistry, rebind):
    async def bind_key_tool(args: dict) -> dict:
        key = args["key"]
        action = args["action"]
        bind_args = args.get("args", {})
        try:
            actions.get(action)
        except KeyError:
            return {"content": [{"type": "text", "text": f"Unknown action: {action}"}]}
        cfg = config_store.load()
        cfg.bindings[key] = KeyBinding(action=action, args=dict(bind_args))
        config_store.save(cfg)
        if rebind is not None:
            rebind()
        return {"content": [{"type": "text", "text": f"Bound {key!r} → {action}."}]}
    return bind_key_tool


def _unbind_key_handler(config_store: ConfigStore, rebind):
    async def unbind_key_tool(args: dict) -> dict:
        key = args["key"]
        cfg = config_store.load()
        if key in cfg.bindings:
            del cfg.bindings[key]
            config_store.save(cfg)
            if rebind is not None:
                rebind()
            return {"content": [{"type": "text", "text": f"Unbound {key!r}."}]}
        return {"content": [{"type": "text", "text": f"No binding for {key!r}."}]}
    return unbind_key_tool


def _set_config_handler(config_store: ConfigStore):
    async def set_config_tool(args: dict) -> dict:
        path = args["path"]
        value = args["value"]
        cfg = config_store.load()
        try:
            cfg.set_path(path, value)
        except KeyError:
            return {"content": [{"type": "text", "text": f"Unknown config path: {path}"}]}
        config_store.save(cfg)
        return {"content": [{"type": "text", "text": f"Set {path} = {value!r}."}]}
    return set_config_tool


def _get_config_handler(config_store: ConfigStore):
    async def get_config_tool(args: dict) -> dict:
        path = args["path"]
        cfg = config_store.load()
        try:
            value = cfg.get_path(path)
        except KeyError:
            return {"content": [{"type": "text", "text": f"Unknown config path: {path}"}]}
        return {"content": [{"type": "text", "text": json.dumps(value)}]}
    return get_config_tool


def _list_actions_handler(actions: ActionRegistry):
    async def list_actions_tool(_args: dict) -> dict:
        out = [
            {"name": s.name, "description": s.description, "args_schema": list(s.args_schema.keys())}
            for s in actions.list()
        ]
        return {"content": [{"type": "text", "text": json.dumps(out, indent=2)}]}
    return list_actions_tool


def _list_bindings_handler(config_store: ConfigStore):
    async def list_bindings_tool(_args: dict) -> dict:
        cfg = config_store.load()
        out = [
            {"key": k, "action": b.action, "args": b.args}
            for k, b in sorted(cfg.bindings.items())
        ]
        return {"content": [{"type": "text", "text": json.dumps(out, indent=2)}]}
    return list_bindings_tool


def _list_widgets_handler(registry: WidgetRegistry):
    async def list_widgets_tool(_args: dict) -> dict:
        out = []
        for info in registry.describe_all():
            out.append({
                "name": info.name,
                "description": info.description,
                "props_schema": {k: getattr(v, "__name__", str(v))
                                 for k, v in info.props_schema.items()},
            })
        return {"content": [{"type": "text", "text": json.dumps(out, indent=2)}]}
    return list_widgets_tool


def _get_layout_handler(current_layout, widget_registry: WidgetRegistry, app=None):
    from mod_tui.layout.titles import populate_effective_titles

    async def get_layout_tool(args: dict) -> dict:
        target_tab_id = (args or {}).get("tab_id")
        spec = None
        tab_title = None
        tab_id = None
        if app is not None:
            ws = getattr(app, "_workspace", None)
            tid = target_tab_id or getattr(app, "_active_tab_id", None)
            if ws is not None and tid is not None:
                tab = next((t for t in ws.tabs if t.id == tid), None)
                if tab is not None:
                    spec = tab.layout
                    tab_id = tab.id
                    tab_title = tab.title
        if spec is None:
            spec = current_layout() if current_layout is not None else None
        if spec is None:
            return {"content": [{"type": "text", "text": "No layout applied yet."}]}
        dumped = spec.model_dump(mode="json")
        try:
            populate_effective_titles(dumped["layout"], widget_registry)
        except Exception:
            pass  # Titles are advisory; never block the dump.
        out = {"tab_id": tab_id, "tab_title": tab_title, "spec": dumped}
        return {"content": [{"type": "text", "text": json.dumps(out, indent=2)}]}

    return get_layout_tool


def _set_theme_handler(app):
    async def set_theme_tool(args: dict) -> dict:
        try:
            spec = ThemeSpec.model_validate(args["spec"])
        except Exception as e:
            return {"content": [{"type": "text", "text": f"Invalid ThemeSpec: {e}"}]}
        try:
            await apply_theme(app, spec, theme_name="<inline>")
        except Exception as e:
            return {"content": [{"type": "text", "text": f"Apply error: {e}"}]}
        return {"content": [{"type": "text", "text": "Theme applied."}]}
    return set_theme_tool


def _save_theme_handler(themes_store: NamedThemesStore, app):
    async def save_theme_tool(args: dict) -> dict:
        name = args["name"]
        if "spec" in args:
            try:
                spec = ThemeSpec.model_validate(args["spec"])
            except Exception as e:
                return {"content": [{"type": "text", "text": f"Invalid ThemeSpec: {e}"}]}
        else:
            try:
                palette = palette_from_textual_theme(app.current_theme)
            except Exception as e:
                return {"content": [{"type": "text",
                                     "text": f"Could not snapshot active theme: {e}"}]}
            extra = getattr(app, "_active_theme_extra_css", "") or ""
            spec = ThemeSpec(palette=palette, extra_css=extra)
        try:
            themes_store.save(name, spec)
        except ValueError as e:
            return {"content": [{"type": "text", "text": f"Invalid theme name: {e}"}]}
        return {"content": [{"type": "text", "text": f"Saved theme {name!r}."}]}
    return save_theme_tool


def _load_theme_handler(themes_store: NamedThemesStore, app, config_store=None):
    async def load_theme_tool(args: dict) -> dict:
        name = args["name"]
        persist = bool(args.get("persist", True))
        scope = args.get("scope", "global")
        if scope not in ("global", "project"):
            return {"content": [{"type": "text",
                                 "text": f"Invalid scope: {scope!r} (use 'global' or 'project')"}]}
        # 1. Try saved store.
        spec = themes_store.load(name)
        if spec is not None:
            try:
                await apply_theme(app, spec, theme_name=name)
            except Exception as e:
                return {"content": [{"type": "text", "text": f"Apply error: {e}"}]}
        else:
            # 2. Fall through to Textual built-ins.
            try:
                available = app.available_themes
            except Exception:
                available = {}
            if name not in available:
                return {"content": [{"type": "text", "text": f"Theme not found: {name}"}]}
            # Built-in pass-through: clear our extra_css source, set theme directly.
            if _EXTRA_CSS_KEY in app.stylesheet.source:
                del app.stylesheet.source[_EXTRA_CSS_KEY]
            app._active_theme_extra_css = ""
            app.theme = name
            try:
                app.refresh_css()
            except Exception:
                pass

        # 3. Persist active-theme pointer if asked.
        warnings: list[str] = []
        if persist:
            if scope == "global":
                if config_store is not None:
                    cfg = config_store.load()
                    cfg.ui.active_theme = name
                    config_store.save(cfg)
                else:
                    warnings.append("persist requested but no config_store available")
            elif scope == "project":
                ws = getattr(app, "_workspace", None)
                if ws is not None:
                    ws = ws.model_copy(update={"active_theme": name})
                    app._workspace = ws
                    save_workspace(app.cwd, ws)
                else:
                    warnings.append("persist requested but no workspace available")

        msg = f"Loaded theme {name!r}."
        if warnings:
            msg += " Warning: " + "; ".join(warnings) + "."
        return {"content": [{"type": "text", "text": msg}]}
    return load_theme_tool


def _list_themes_handler(themes_store: NamedThemesStore, app):
    async def list_themes_tool(_args: dict) -> dict:
        saved = themes_store.list()
        try:
            builtin = sorted(
                n for n in app.available_themes.keys()
                if not n.startswith("mod_tui:")
            )
        except Exception:
            builtin = []
        active = getattr(app, "theme", None) or ""
        # Strip the "mod_tui:" prefix from active for user-facing display
        # so a saved theme named "alpha" reads back as "alpha".
        if active.startswith("mod_tui:"):
            active_display = active[len("mod_tui:"):]
        else:
            active_display = active
        payload = {"saved": saved, "builtin": builtin, "active": active_display}
        return {"content": [{"type": "text", "text": json.dumps(payload)}]}
    return list_themes_tool


def _get_theme_handler(themes_store: NamedThemesStore, app):
    async def get_theme_tool(args: dict) -> dict:
        name = (args or {}).get("name")
        if name:
            spec = themes_store.load(name)
            if spec is None:
                return {"content": [{"type": "text", "text": f"Theme not found: {name}"}]}
            return {"content": [{"type": "text", "text": json.dumps(spec.model_dump(mode="json"))}]}
        # No name → snapshot the active theme.
        try:
            palette = palette_from_textual_theme(app.current_theme).model_dump(mode="json")
        except Exception as e:
            return {"content": [{"type": "text", "text": f"Cannot read active theme: {e}"}]}
        active = getattr(app, "theme", "") or ""
        if active.startswith("mod_tui:"):
            active = active[len("mod_tui:"):]
        extra = getattr(app, "_active_theme_extra_css", "") or ""
        payload = {"name": active, "palette": palette, "extra_css": extra}
        return {"content": [{"type": "text", "text": json.dumps(payload)}]}
    return get_theme_tool


_SPECS: list[_ToolSpec] = [
    _ToolSpec(
        name="spawn_agent",
        description=(
            "Spawn a new Claude Code child agent. `name` is a short label "
            "for the table; `prompt` is the initial task. Optional `cwd` "
            "overrides the working directory; optional `allowed_tools` is a "
            "list of tool names to whitelist for this child (defaults to "
            "inheriting the user's settings.json)."
        ),
        # Use a full JSON Schema dict so the SDK's pass-through path is
        # triggered (it checks for "type" + "properties" keys).  This lets us
        # mark cwd / allowed_tools as optional (absent from "required") while
        # still advertising them to the orchestrator AI.
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "prompt": {"type": "string"},
                "cwd": {"type": "string"},
                "allowed_tools": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "required": ["name", "prompt"],
        },
        build=_spawn_handler,
    ),
    _ToolSpec(
        name="list_agents",
        description="List all currently registered agents and their states.",
        input_schema={},
        build=_list_handler,
    ),
    _ToolSpec(
        name="read_agent_transcript",
        description="Read the full transcript of an agent by id.",
        input_schema={"agent_id": str},
        build=_read_handler,
    ),
    _ToolSpec(
        name="send_to_agent",
        description=(
            "Send a follow-up message to an existing agent. The agent will "
            "process it as a new turn."
        ),
        input_schema={"agent_id": str, "message": str},
        build=_send_handler,
    ),
    _ToolSpec(
        name="interrupt_agent",
        description="Interrupt the agent's current generation, if any.",
        input_schema={"agent_id": str},
        build=_interrupt_handler,
    ),
    _ToolSpec(
        name="kill_agent",
        description="Stop and remove an agent session.",
        input_schema={"agent_id": str},
        build=_kill_handler,
    ),
    _ToolSpec(
        name="respond_to_agent_request",
        description=(
            "Respond to an agent's pending ask_orchestrator request, "
            "identified by request_id."
        ),
        input_schema={"agent_id": str, "request_id": str, "response": str},
        build=_respond_handler,
    ),
]


def build_orchestrator_tools(
    manager: AgentManager,
    *,
    apply_layout=None,
    layouts_store: NamedLayoutsStore | None = None,
    themes_store: NamedThemesStore | None = None,
    config_store: ConfigStore | None = None,
    actions: ActionRegistry | None = None,
    rebind_keys=None,
    widget_registry: WidgetRegistry | None = None,
    current_layout=None,
    app=None,
):
    """Return a dict {tool_name: async_handler} for unit testing.

    apply_layout: async callable (spec, *, layout_name=None) -> None applying a
    LayoutSpec to the live UI. If None, set_layout / load_layout are omitted.
    layouts_store: NamedLayoutsStore for save/load/list. If None, the
    save/load/list tools are omitted.
    config_store + actions: if both provided, config/keybinding tools are added.
    rebind_keys: optional callable invoked after any keybinding change.
    widget_registry: if provided, a list_widgets tool is added.
    app: if provided, tab management tools (add_tab, close_tab, switch_tab,
    list_tabs) are registered.
    """
    handlers: dict = {}
    for spec in _SPECS:
        handlers[spec.name] = spec.build(manager)
    if apply_layout is not None and layouts_store is not None:
        handlers["set_layout"] = _set_layout_handler(apply_layout, widget_registry)
        handlers["save_layout"] = _save_layout_handler(layouts_store, app=app)
        handlers["load_layout"] = _load_layout_handler(apply_layout, layouts_store, app=app)
        handlers["list_layouts"] = _list_layouts_handler(layouts_store)
    if config_store is not None and actions is not None:
        handlers["bind_key"] = _bind_key_handler(config_store, actions, rebind_keys)
        handlers["unbind_key"] = _unbind_key_handler(config_store, rebind_keys)
        handlers["set_config"] = _set_config_handler(config_store)
        handlers["get_config"] = _get_config_handler(config_store)
        handlers["list_actions"] = _list_actions_handler(actions)
        handlers["list_bindings"] = _list_bindings_handler(config_store)
    if widget_registry is not None:
        handlers["list_widgets"] = _list_widgets_handler(widget_registry)
    if widget_registry is not None and current_layout is not None:
        handlers["get_layout"] = _get_layout_handler(current_layout, widget_registry, app=app)
    if themes_store is not None and app is not None:
        handlers["set_theme"] = _set_theme_handler(app)
        handlers["save_theme"] = _save_theme_handler(themes_store, app)
        handlers["load_theme"] = _load_theme_handler(
            themes_store, app, config_store=config_store,
        )
        handlers["list_themes"] = _list_themes_handler(themes_store, app)
        handlers["get_theme"] = _get_theme_handler(themes_store, app)
    if app is not None:
        handlers["add_tab"] = add_tab_handler(app)
        handlers["close_tab"] = close_tab_handler(app)
        handlers["switch_tab"] = switch_tab_handler(app)
        handlers["list_tabs"] = list_tabs_handler(app)
        handlers["rename_tab"] = rename_tab_handler(app)
        handlers["reorder_tabs"] = reorder_tabs_handler(app)
    return handlers


def build_orchestrator_mcp_server(
    manager: AgentManager,
    *,
    apply_layout=None,
    layouts_store: NamedLayoutsStore | None = None,
    themes_store: NamedThemesStore | None = None,
    config_store: ConfigStore | None = None,
    actions: ActionRegistry | None = None,
    rebind_keys=None,
    widget_registry: WidgetRegistry | None = None,
    current_layout=None,
    app=None,
):
    sdk_tools = []
    for spec in _SPECS:
        handler = spec.build(manager)
        decorated = tool(spec.name, spec.description, spec.input_schema)(handler)
        sdk_tools.append(decorated)
    if apply_layout is not None and layouts_store is not None:
        layout_specs = [
            (
                "set_layout",
                "Edit the **active** tab's layout (or pass `tab_id` to "
                "target a specific tab). Use add_tab to create new tabs "
                "instead of inserting OrchestratorChat panels. Each panel "
                "may set an optional `title` field; the user references "
                "panels by title in chat. Call get_layout first to "
                "discover effective titles. Spec format supports a new "
                "node type `{type: 'tabs', children: [Panel, ...], "
                "active: '<panel_id>'}` for panel-level tabs (each tab "
                "holds exactly one widget). "
                "If `spec.custom_widgets` is present, each entry's `source` "
                "string is **exec'd in-process with full Python privileges** "
                "to register a new Widget class before the layout is applied. "
                "Only ship `custom_widgets` source you have personally "
                "authored — anything you exec here can read files, hit the "
                "network, and execute arbitrary code with the user's "
                "permissions. The built-in widgets (list_widgets) are safer.",
                {"spec": dict, "tab_id": str},
                _set_layout_handler(apply_layout, widget_registry),
            ),
            (
                "save_layout",
                "Save a LayoutSpec under a name in ~/.config/mod_tui/layouts/. "
                "If `spec` is omitted, saves the active tab's current layout "
                "(or the tab named by `tab_id`).",
                {"name": str, "spec": dict, "tab_id": str},
                _save_layout_handler(layouts_store, app=app),
            ),
            (
                "load_layout",
                "Load a saved layout by name and apply it. By default it "
                "replaces the active tab's spec. Pass `tab_id` to target a "
                "specific tab. Pass `as_new_tab: true` to create a new tab "
                "seeded from the named layout instead (use `title` to label "
                "the new tab; defaults to the layout name).",
                {"name": str, "tab_id": str, "as_new_tab": bool, "title": str},
                _load_layout_handler(apply_layout, layouts_store, app=app),
            ),
            (
                "list_layouts",
                "List the names of all saved layouts.",
                {},
                _list_layouts_handler(layouts_store),
            ),
        ]
        for name, desc, schema, handler in layout_specs:
            sdk_tools.append(tool(name, desc, schema)(handler))
    if themes_store is not None and app is not None:
        theme_specs = [
            (
                "set_theme",
                "Apply a ThemeSpec to the live app. The spec is "
                "{ palette: {primary, secondary, warning, error, success, accent, "
                "foreground, background, surface, panel, boost, dark, "
                "luminosity_spread, text_alpha, variables}, extra_css: str }. "
                "Color strings follow Textual's syntax (#rrggbb or named). "
                "If `extra_css` is present, it is parsed at app scope; bad "
                "CSS is rejected before the palette change. Only ship "
                "`extra_css` you have personally authored — CSS can hide "
                "chrome, fake widgets, or break input visibility. Does NOT "
                "persist; use save_theme + load_theme for that.",
                {"spec": dict},
                _set_theme_handler(app),
            ),
            (
                "save_theme",
                "Save a ThemeSpec to ~/.config/mod_tui/themes/<name>.json. "
                "If `spec` is omitted, snapshots the currently-active palette "
                "and the last applied extra_css. Use this to capture the "
                "live look as a named theme.",
                {"name": str, "spec": dict},
                _save_theme_handler(themes_store, app),
            ),
            (
                "load_theme",
                "Load a saved theme by name and apply it. Falls through to "
                "Textual built-ins (textual-dark, nord, gruvbox, dracula, "
                "catppuccin-*, …) if the name is not in the saved store. "
                "When `persist` (default true) the active-theme pointer is "
                "written: `scope='global'` writes ~/.config/mod_tui/config.toml "
                "ui.active_theme; `scope='project'` writes workspace.json's "
                "active_theme. Default scope is 'global'.",
                {"name": str, "persist": bool, "scope": str},
                _load_theme_handler(themes_store, app, config_store=config_store),
            ),
            (
                "list_themes",
                "Return {saved, builtin, active}. `saved` is the user's "
                "named themes; `builtin` is Textual's built-in themes "
                "(read-only); `active` is the current theme name (without "
                "the internal mod_tui: prefix).",
                {},
                _list_themes_handler(themes_store, app),
            ),
            (
                "get_theme",
                "Return a saved theme's full spec when `name` is given. "
                "Without `name`, returns the active theme as "
                "{name, palette, extra_css}. Pass the result back through "
                "set_theme to apply edits.",
                {"name": str},
                _get_theme_handler(themes_store, app),
            ),
        ]
        for name, desc, schema, handler in theme_specs:
            sdk_tools.append(tool(name, desc, schema)(handler))
    if config_store is not None and actions is not None:
        config_specs = [
            (
                "bind_key",
                "Bind a key (e.g., 'ctrl+x', '~') to a registered action. "
                "Optional `args` dict is passed to the action when invoked.",
                {"key": str, "action": str},
                _bind_key_handler(config_store, actions, rebind_keys),
            ),
            (
                "unbind_key",
                "Remove the binding for the given key.",
                {"key": str},
                _unbind_key_handler(config_store, rebind_keys),
            ),
            (
                "set_config",
                "Set a config value by dotted path (e.g., 'ui.active_theme').",
                {"path": str, "value": str},
                _set_config_handler(config_store),
            ),
            (
                "get_config",
                "Read a config value by dotted path. Returns the value as JSON.",
                {"path": str},
                _get_config_handler(config_store),
            ),
            (
                "list_actions",
                "List all registered keybinding actions.",
                {},
                _list_actions_handler(actions),
            ),
            (
                "list_bindings",
                "List all current keybindings.",
                {},
                _list_bindings_handler(config_store),
            ),
        ]
        for name, desc, schema, handler in config_specs:
            sdk_tools.append(tool(name, desc, schema)(handler))
    if widget_registry is not None:
        sdk_tools.append(tool(
            "list_widgets",
            "List all widgets registered in the layout registry, with their "
            "descriptions and prop schemas. Use this to discover what widgets "
            "you can include in a set_layout call.",
            {},
        )(_list_widgets_handler(widget_registry)))
    if widget_registry is not None and current_layout is not None:
        sdk_tools.append(tool(
            "get_layout",
            "Returns the active tab's LayoutSpec as JSON, alongside `tab_id` "
            "and `tab_title`. Each panel's `title` field is populated to its "
            "effective on-screen value. Pass `tab_id` to inspect a specific "
            "tab. Pass the `spec` field's value back through `set_layout` to "
            "edit the tab.",
            {"tab_id": str},
        )(_get_layout_handler(current_layout, widget_registry, app=app)))
    if app is not None:
        sdk_tools.append(tool(
            "add_tab",
            "Create a new app-level tab. `title` is the user-facing label "
            "on the tab strip. Optional `layout` may be a LayoutSpec dict, "
            "the name of a saved layout (resolved from the named-layouts "
            "store), or omitted (a default seed is used). Optional "
            "`activate` (default true) makes the new tab the active one. "
            "Returns the new tab id.",
            {"title": str, "layout": dict, "activate": bool},
        )(add_tab_handler(app)))
        sdk_tools.append(tool(
            "close_tab",
            "Close the tab with the given id. Refuses if it would leave "
            "the workspace with zero OrchestratorChat panels (returns a "
            "structured error so you can add chat to another tab first). "
            "Refuses if it's the last tab.",
            {"tab_id": str},
        )(close_tab_handler(app)))
        sdk_tools.append(tool(
            "switch_tab",
            "Make the tab with the given id the active one.",
            {"tab_id": str},
        )(switch_tab_handler(app)))
        sdk_tools.append(tool(
            "list_tabs",
            "List all tabs with id, title, active flag, has_chat flag, "
            "and the list of panel ids contained in each tab.",
            {},
        )(list_tabs_handler(app)))
        sdk_tools.append(tool(
            "rename_tab",
            "Rename an existing tab. `tab_id` identifies the tab; `title` "
            "is the new user-facing label shown in the tab strip. The "
            "underlying widgets are not re-mounted, so panel state is "
            "preserved.",
            {"tab_id": str, "title": str},
        )(rename_tab_handler(app)))
        sdk_tools.append(tool(
            "reorder_tabs",
            "Rearrange the tab strip. `tab_ids` must be a permutation of "
            "the existing tab ids — every current id must appear exactly "
            "once. The active tab stays active (just at a new position). "
            "Widget state is preserved across the reorder.",
            {"tab_ids": list},
        )(reorder_tabs_handler(app)))
    return create_sdk_mcp_server(
        name="mod_tui_orchestrator",
        version="1.0.0",
        tools=sdk_tools,
    )
