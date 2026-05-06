import asyncio

from claude_agent_sdk import create_sdk_mcp_server, tool

from mod_tui.agents.request_inbox import RequestInbox
from mod_tui.events import (
    AgentNotifiedOrchestrator,
    AgentRequestedUserInput,
    EventBus,
)


def build_child_tools(*, agent_id: str, bus: EventBus, inbox: RequestInbox):
    """Return (notify_handler, ask_handler) — bare async callables for unit tests."""

    async def notify_orchestrator(args: dict) -> dict:
        message = args["message"]
        bus.publish(AgentNotifiedOrchestrator(agent_id=agent_id, message=message))
        return {"content": [{"type": "text", "text": "Notification delivered."}]}

    async def ask_orchestrator(args: dict) -> dict:
        question = args["question"]
        timeout_s = float(args.get("timeout_s", 300))
        request_id = inbox.register()
        bus.publish(
            AgentRequestedUserInput(
                agent_id=agent_id, question=question, request_id=request_id
            )
        )
        try:
            response = await inbox.wait(request_id, timeout_s=timeout_s)
            return {"content": [{"type": "text", "text": response}]}
        except asyncio.TimeoutError:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"ask_orchestrator timed out after {timeout_s}s "
                            "with no response."
                        ),
                    }
                ]
            }

    return notify_orchestrator, ask_orchestrator


def build_child_mcp_server(*, agent_id: str, bus: EventBus, inbox: RequestInbox):
    notify_h, ask_h = build_child_tools(agent_id=agent_id, bus=bus, inbox=inbox)
    notify = tool(
        "notify_orchestrator",
        "Send a fire-and-forget notification to the orchestrator.",
        {"message": str},
    )(notify_h)
    ask = tool(
        "ask_orchestrator",
        (
            "Ask the orchestrator a question and block until they respond. "
            "Optional timeout_s defaults to 300 seconds."
        ),
        {"question": str},  # timeout_s is optional, not in schema
    )(ask_h)
    return create_sdk_mcp_server(
        name="mod_tui_child", version="1.0.0", tools=[notify, ask]
    )
