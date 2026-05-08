from claude_agent_sdk import AssistantMessage, TextBlock, ThinkingBlock, ToolUseBlock


def format_assistant_message(msg: AssistantMessage) -> str:
    parts: list[str] = []
    for block in msg.content:
        if isinstance(block, TextBlock):
            parts.append(block.text)
        elif isinstance(block, ToolUseBlock):
            args = ", ".join(f"{k}={v!r}" for k, v in (block.input or {}).items())
            parts.append(f"[tool: {block.name}]({args})")
        elif isinstance(block, ThinkingBlock):
            # Skipped in plan 2 — too noisy. Plan 5 may render as collapsible.
            continue
    return "".join(parts)
