import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from claude_agent_sdk import create_sdk_mcp_server, tool

from mod_tui.agents.manager import AgentManager
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


def _set_layout_handler(apply_layout):
    async def set_layout_tool(args: dict) -> dict:
        try:
            spec = LayoutSpec.model_validate(args["spec"])
        except Exception as e:
            return {"content": [{"type": "text", "text": f"Invalid LayoutSpec: {e}"}]}
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
):
    """Return bare async handlers (for unit testing).

    apply_layout: async callable (spec, *, layout_name=None) -> None applying a
    LayoutSpec to the live UI. If None, set_layout / load_layout are omitted.
    layouts_store: NamedLayoutsStore for save/load/list. If None, the
    save/load/list tools are omitted.
    """
    handlers = [spec.build(manager) for spec in _SPECS]
    if apply_layout is not None and layouts_store is not None:
        handlers.append(_set_layout_handler(apply_layout))
        handlers.append(_save_layout_handler(layouts_store))
        handlers.append(_load_layout_handler(apply_layout, layouts_store))
        handlers.append(_list_layouts_handler(layouts_store))
    return tuple(handlers)


def build_orchestrator_mcp_server(
    manager: AgentManager,
    *,
    apply_layout=None,
    layouts_store: NamedLayoutsStore | None = None,
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
                "Replace the current UI layout with the given LayoutSpec dict.",
                {"spec": dict},
                _set_layout_handler(apply_layout),
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
    return create_sdk_mcp_server(
        name="mod_tui_orchestrator",
        version="1.0.0",
        tools=sdk_tools,
    )
