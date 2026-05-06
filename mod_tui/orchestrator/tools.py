import json

from claude_agent_sdk import create_sdk_mcp_server, tool

from mod_tui.agents.manager import AgentManager


def build_orchestrator_tools(manager: AgentManager):
    """Return the three raw async handler callables for unit testing.

    NOTE: The @tool decorator returns an SdkMcpTool dataclass (not callable).
    This function returns the .handler attributes so tests and direct callers
    can invoke them as plain async functions.

    For wiring into the SDK MCP server, call build_orchestrator_mcp_server(manager).
    """

    @tool(
        "spawn_agent",
        "Spawn a new Claude Code child agent with the given name and initial "
        "prompt. Returns the agent id.",
        {
            "name": str,
            "prompt": str,
        },
    )
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

    @tool(
        "list_agents",
        "List all currently registered agents and their states.",
        {},
    )
    async def list_agents(_args: dict) -> dict:
        infos = [info.to_dict() for info in manager.list_infos()]
        return {
            "content": [{"type": "text", "text": json.dumps(infos, indent=2)}]
        }

    @tool(
        "read_agent_transcript",
        "Read the full transcript of an agent by id.",
        {
            "agent_id": str,
        },
    )
    async def read_agent_transcript(args: dict) -> dict:
        entries = manager.read_transcript(args["agent_id"])
        text = "\n".join(f"[{e.role}] {e.text}" for e in entries)
        return {"content": [{"type": "text", "text": text}]}

    # @tool returns SdkMcpTool (a dataclass, not callable).
    # Return the raw handler callables so callers can invoke them directly.
    return spawn_agent.handler, list_agents.handler, read_agent_transcript.handler


def build_orchestrator_mcp_server(manager: AgentManager):
    """Build and return the in-process MCP server for the orchestrator.

    The three tools are re-decorated so create_sdk_mcp_server receives
    proper SdkMcpTool objects.
    """
    spawn_handler, list_handler, read_handler = build_orchestrator_tools(manager)

    @tool(
        "spawn_agent",
        "Spawn a new Claude Code child agent with the given name and initial "
        "prompt. Returns the agent id.",
        {"name": str, "prompt": str},
    )
    async def spawn_agent(args: dict) -> dict:
        return await spawn_handler(args)

    @tool(
        "list_agents",
        "List all currently registered agents and their states.",
        {},
    )
    async def list_agents(_args: dict) -> dict:
        return await list_handler(_args)

    @tool(
        "read_agent_transcript",
        "Read the full transcript of an agent by id.",
        {"agent_id": str},
    )
    async def read_agent_transcript(args: dict) -> dict:
        return await read_handler(args)

    return create_sdk_mcp_server(
        name="mod_tui_orchestrator",
        version="1.0.0",
        tools=[spawn_agent, list_agents, read_agent_transcript],
    )
