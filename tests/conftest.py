import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock


@pytest.fixture
def ok_script():
    """A factory returning a 2-message SDK script: one assistant TextBlock + a
    success ResultMessage. Reusable across tests; defaults to 'done'."""
    def _make(text: str = "done") -> list:
        return [
            AssistantMessage(content=[TextBlock(text=text)], model="fake-model"),
            ResultMessage(
                subtype="success",
                duration_ms=1,
                duration_api_ms=1,
                is_error=False,
                num_turns=1,
                session_id="fake",
                total_cost_usd=0.0,
                usage={"input_tokens": 1, "output_tokens": 1},
                result=text,
            ),
        ]
    return _make
