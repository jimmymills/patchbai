import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from claude_agent_sdk import create_sdk_mcp_server, tool

from mod_tui.actions import ActionRegistry
from mod_tui.agents.manager import AgentManager
from mod_tui.config import ConfigStore, KeyBinding
from mod_tui.layout.registry import WidgetRegistry
from mod_tui.layout.spec import LayoutSpec
from mod_tui.persistence.layouts_store import NamedLayoutsStore


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
            await apply_layout(spec)
        except Exception as e:
            return {"content": [{"type": "text", "text": f"Apply error: {e}"}]}
        return {"content": [{"type": "text", "text": "Layout applied."}]}
    return set_layout_tool


def _save_layout_handler(layouts_store: NamedLayoutsStore):
    async def save_layout_tool(args: dict) -> dict:
        name = args["name"]
        try:
            spec = LayoutSpec.model_validate(args["spec"])
        except Exception as e:
            return {"content": [{"type": "text", "text": f"Invalid LayoutSpec: {e}"}]}
        try:
            layouts_store.save(name, spec)
        except ValueError as e:
            return {"content": [{"type": "text", "text": f"Invalid layout name: {e}"}]}
        return {"content": [{"type": "text", "text": f"Saved layout {name!r}."}]}
    return save_layout_tool


def _load_layout_handler(apply_layout, layouts_store: NamedLayoutsStore):
    async def load_layout_tool(args: dict) -> dict:
        name = args["name"]
        spec = layouts_store.load(name)
        if spec is None:
            return {"content": [{"type": "text", "text": f"Layout not found: {name}"}]}
        try:
            await apply_layout(spec, layout_name=name)
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


def _get_layout_handler(current_layout, widget_registry: WidgetRegistry):
    from mod_tui.layout.titles import populate_effective_titles

    async def get_layout_tool(_args: dict) -> dict:
        spec = current_layout() if current_layout is not None else None
        if spec is None:
            return {"content": [{"type": "text", "text": "No layout applied yet."}]}
        dumped = spec.model_dump(mode="json")
        try:
            populate_effective_titles(dumped["layout"], widget_registry)
        except Exception:
            pass  # Titles are advisory; never block the dump.
        return {"content": [{"type": "text", "text": json.dumps(dumped, indent=2)}]}

    return get_layout_tool


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
    config_store: ConfigStore | None = None,
    actions: ActionRegistry | None = None,
    rebind_keys=None,
    widget_registry: WidgetRegistry | None = None,
    current_layout=None,
):
    """Return a dict {tool_name: async_handler} for unit testing.

    apply_layout: async callable (spec, *, layout_name=None) -> None applying a
    LayoutSpec to the live UI. If None, set_layout / load_layout are omitted.
    layouts_store: NamedLayoutsStore for save/load/list. If None, the
    save/load/list tools are omitted.
    config_store + actions: if both provided, config/keybinding tools are added.
    rebind_keys: optional callable invoked after any keybinding change.
    widget_registry: if provided, a list_widgets tool is added.
    """
    handlers: dict = {}
    for spec in _SPECS:
        handlers[spec.name] = spec.build(manager)
    if apply_layout is not None and layouts_store is not None:
        handlers["set_layout"] = _set_layout_handler(apply_layout, widget_registry)
        handlers["save_layout"] = _save_layout_handler(layouts_store)
        handlers["load_layout"] = _load_layout_handler(apply_layout, layouts_store)
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
        handlers["get_layout"] = _get_layout_handler(current_layout, widget_registry)
    return handlers


def build_orchestrator_mcp_server(
    manager: AgentManager,
    *,
    apply_layout=None,
    layouts_store: NamedLayoutsStore | None = None,
    config_store: ConfigStore | None = None,
    actions: ActionRegistry | None = None,
    rebind_keys=None,
    widget_registry: WidgetRegistry | None = None,
    current_layout=None,
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
                "Replace the current UI layout with the given LayoutSpec dict. "
                "Each panel may set an optional `title` field that overrides the widget's "
                "default border title; titles are how the user refers to panels in chat "
                "(e.g., 'make the Activity Panel 2x its size'). Call `get_layout` first "
                "to discover effective titles before mutating. "
                "If `spec.custom_widgets` is present, each entry's `source` "
                "string is **exec'd in-process with full Python privileges** "
                "to register a new Widget class before the layout is applied. "
                "Only ship `custom_widgets` source you have personally "
                "authored — anything you exec here can read files, hit the "
                "network, and execute arbitrary code with the user's "
                "permissions. The built-in widgets (list_widgets) are safer.",
                {"spec": dict},
                _set_layout_handler(apply_layout, widget_registry),
            ),
            (
                "save_layout",
                "Save the given LayoutSpec under a name in ~/.config/mod_tui/layouts/.",
                {"name": str, "spec": dict},
                _save_layout_handler(layouts_store),
            ),
            (
                "load_layout",
                "Load and apply a previously-saved layout by name.",
                {"name": str},
                _load_layout_handler(apply_layout, layouts_store),
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
                "Set a config value by dotted path (e.g., 'ui.theme').",
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
            "Returns the currently applied LayoutSpec as JSON. Each panel's "
            "`title` field is populated to its effective on-screen value, so "
            "you can match a user reference like 'the Activity Panel' against "
            "`title` to find the panel `id` you want to edit. Pass the "
            "modified spec back through `set_layout`. Note: the returned "
            "`title` is the resolved value (defaults if no override was set). "
            "If you only want to change a panel's size or props, set its "
            "`title` field back to null in the spec you pass to `set_layout` "
            "to avoid freezing the resolved title as an explicit override.",
            {},
        )(_get_layout_handler(current_layout, widget_registry)))
    return create_sdk_mcp_server(
        name="mod_tui_orchestrator",
        version="1.0.0",
        tools=sdk_tools,
    )
