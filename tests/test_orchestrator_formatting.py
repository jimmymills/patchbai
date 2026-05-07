from claude_agent_sdk import AssistantMessage, TextBlock, ThinkingBlock, ToolUseBlock

from patchbai.orchestrator.formatting import format_assistant_message


def _msg(blocks: list) -> AssistantMessage:
    return AssistantMessage(content=blocks, model="fake-model")


def test_text_blocks_are_concatenated():
    out = format_assistant_message(_msg([TextBlock(text="hello "), TextBlock(text="world")]))
    assert out == "hello world"


def test_tool_use_block_becomes_inline_marker():
    msg = _msg([
        TextBlock(text="running it: "),
        ToolUseBlock(id="t1", name="bash", input={"command": "ls /tmp"}),
    ])
    out = format_assistant_message(msg)
    assert "running it: " in out
    assert "[tool: bash]" in out
    assert "ls /tmp" in out


def test_thinking_blocks_are_dropped():
    msg = _msg([
        ThinkingBlock(thinking="planning…", signature="sig"),
        TextBlock(text="answer"),
    ])
    assert format_assistant_message(msg) == "answer"


def test_empty_message_returns_empty_string():
    assert format_assistant_message(_msg([])) == ""
