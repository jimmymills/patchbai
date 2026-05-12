"""End-to-end tests for the AskUserQuestion inline-block flow.

Covers:
- The orchestrator's `can_use_tool` intercept for `AskUserQuestion`:
  publishes `AskUserQuestionRequested`, awaits the answer, and returns a
  `PermissionResultDeny(message=<formatted answer>)` so the SDK forwards
  the user's choice to the model as the tool_result content.
- The inline `_AskUserQuestionBlock` widget rendering in `RichTranscript`:
  question text + options are visible; clicking an option button selects
  it; Submit publishes `AskUserQuestionAnswered` with the picked options.
- Multi-select happy path.
- Free-form "Other" custom-text path.
"""
import asyncio
from pathlib import Path

import pytest
from textual.app import App
from textual.widgets import Button, Input

from patchfeld.agents.fake_sdk_adapter import FakeSDKAdapter
from patchfeld.agents.manager import AgentManager
from patchfeld.agents.permission_grants import PermissionGrants
from patchfeld.events import (
    AskUserQuestionAnswered,
    AskUserQuestionRequested,
    EventBus,
)
from patchfeld.orchestrator.session import OrchestratorSession
from patchfeld.widgets.rich_transcript import (
    RichTranscript,
    _AskUserQuestionBlock,
)


# ---------- widget-level tests ----------------------------------------


class _HostApp(App):
    def __init__(self, bus: EventBus, agent_id: str) -> None:
        super().__init__()
        self.event_bus = bus
        self._agent_id = agent_id

    def compose(self):
        yield RichTranscript(agent_id=self._agent_id, event_bus=self.event_bus)


@pytest.mark.asyncio
async def test_ask_user_question_renders_question_and_options(tmp_path: Path):
    bus = EventBus()
    app = _HostApp(bus, "orchestrator")
    app.cwd = tmp_path
    async with app.run_test(size=(120, 60)) as pilot:
        await pilot.pause()
        # Open a turn first so the block has a parent.
        bus.publish(_msg(role="user", text="please ask me"))
        questions = (
            {
                "question": "Which library should we use?",
                "header": "Library",
                "multiSelect": False,
                "options": [
                    {"label": "Lib A", "description": "fast"},
                    {"label": "Lib B", "description": "slow but stable"},
                    {"label": "Lib C", "description": "small footprint"},
                ],
            },
        )
        bus.publish(AskUserQuestionRequested(
            agent_id="orchestrator", request_id="r1",
            tool_id="t1", questions=questions,
        ))
        await pilot.pause()
        block = app.query_one(_AskUserQuestionBlock)
        # Question + header are visible somewhere in the block's static text.
        statics_text = " ".join(
            str(s.content) for s in block.query("Static")
        )
        assert "Which library should we use?" in statics_text
        assert "Library" in statics_text  # header
        # Descriptions render too.
        assert "fast" in statics_text
        assert "small footprint" in statics_text
        # Three option buttons plus one submit button.
        buttons = list(block.query(Button))
        assert len(buttons) == 4
        option_labels = [str(b.label) for b in buttons if b.id and b.id.startswith("ask-opt-")]
        assert any("Lib A" in lbl for lbl in option_labels)
        assert any("Lib B" in lbl for lbl in option_labels)
        assert any("Lib C" in lbl for lbl in option_labels)


@pytest.mark.asyncio
async def test_ask_user_question_single_select_publishes_answer(tmp_path: Path):
    bus = EventBus()
    answered: list[AskUserQuestionAnswered] = []
    bus.subscribe(AskUserQuestionAnswered, answered.append)

    app = _HostApp(bus, "orchestrator")
    app.cwd = tmp_path
    async with app.run_test(size=(120, 60)) as pilot:
        await pilot.pause()
        bus.publish(_msg(role="user", text="go"))
        questions = (
            {
                "question": "Pick one",
                "multiSelect": False,
                "options": [
                    {"label": "Alpha", "description": ""},
                    {"label": "Beta", "description": ""},
                ],
            },
        )
        bus.publish(AskUserQuestionRequested(
            agent_id="orchestrator", request_id="rq", tool_id="t",
            questions=questions,
        ))
        await pilot.pause()
        app.query_one(_AskUserQuestionBlock)
        await pilot.click("#ask-opt-q0-o1", times=1)  # pick Beta
        await pilot.pause()
        await pilot.click("#ask-submit")
        await pilot.pause()

    assert len(answered) == 1
    a = answered[0]
    assert a.request_id == "rq"
    assert a.agent_id == "orchestrator"
    assert a.answers == (
        {"selected": ("Beta",), "custom_text": None},
    )


@pytest.mark.asyncio
async def test_ask_user_question_multi_select_toggles(tmp_path: Path):
    bus = EventBus()
    answered: list[AskUserQuestionAnswered] = []
    bus.subscribe(AskUserQuestionAnswered, answered.append)

    app = _HostApp(bus, "orchestrator")
    app.cwd = tmp_path
    async with app.run_test(size=(120, 60)) as pilot:
        await pilot.pause()
        bus.publish(_msg(role="user", text="multi"))
        questions = (
            {
                "question": "Pick all that apply",
                "multiSelect": True,
                "options": [
                    {"label": "Red"},
                    {"label": "Green"},
                    {"label": "Blue"},
                ],
            },
        )
        bus.publish(AskUserQuestionRequested(
            agent_id="orchestrator", request_id="rq2", tool_id="t",
            questions=questions,
        ))
        await pilot.pause()
        # Pick Red, then Blue, then Red again (toggle off).
        await pilot.click("#ask-opt-q0-o0")
        await pilot.pause()
        await pilot.click("#ask-opt-q0-o2")
        await pilot.pause()
        await pilot.click("#ask-opt-q0-o0")
        await pilot.pause()
        await pilot.click("#ask-submit")
        await pilot.pause()

    assert len(answered) == 1
    assert answered[0].answers == (
        {"selected": ("Blue",), "custom_text": None},
    )


@pytest.mark.asyncio
async def test_ask_user_question_other_custom_text(tmp_path: Path):
    bus = EventBus()
    answered: list[AskUserQuestionAnswered] = []
    bus.subscribe(AskUserQuestionAnswered, answered.append)

    app = _HostApp(bus, "orchestrator")
    app.cwd = tmp_path
    async with app.run_test(size=(120, 60)) as pilot:
        await pilot.pause()
        bus.publish(_msg(role="user", text="custom"))
        questions = (
            {
                "question": "Name your color",
                "multiSelect": False,
                "options": [{"label": "Red"}, {"label": "Green"}],
            },
        )
        bus.publish(AskUserQuestionRequested(
            agent_id="orchestrator", request_id="rq3", tool_id="t",
            questions=questions,
        ))
        await pilot.pause()
        # Type into the Other input directly (no option selected).
        inp = app.query_one("#ask-other-q0", Input)
        inp.value = "magenta"
        await pilot.pause()
        await pilot.click("#ask-submit")
        await pilot.pause()

    assert len(answered) == 1
    assert answered[0].answers == (
        {"selected": (), "custom_text": "magenta"},
    )


@pytest.mark.asyncio
async def test_ask_user_question_block_replaces_normal_tool_call_render(
    tmp_path: Path,
):
    """The default `tool_use` for AskUserQuestion shouldn't render as an
    ordinary _ToolCall — the special inline block replaces it."""
    from patchfeld.widgets.rich_transcript import _ToolCall

    bus = EventBus()
    app = _HostApp(bus, "orchestrator")
    app.cwd = tmp_path
    async with app.run_test(size=(120, 60)) as pilot:
        await pilot.pause()
        bus.publish(_msg(role="user", text="go"))
        # The ask event arrives first (this is the live path: can_use_tool
        # fires before AgentSession streams the tool_use into the transcript
        # — not strictly, but our suppression set handles either order).
        bus.publish(AskUserQuestionRequested(
            agent_id="orchestrator", request_id="rq", tool_id="t-aq",
            questions=({"question": "?", "options": [{"label": "x"}]},),
        ))
        bus.publish(_msg(
            role="tool_use", text="[AskUserQuestion] {...}",
            tool_id="t-aq", tool_name="AskUserQuestion",
        ))
        # And the eventual tool_result (which carries the formatted answer
        # we returned from can_use_tool) should also be suppressed.
        bus.publish(_msg(
            role="tool_result", text="Q1: ?\nA: x", tool_id="t-aq",
        ))
        await pilot.pause()
        tool_calls = list(app.query(_ToolCall))
        assert tool_calls == []
        blocks = list(app.query(_AskUserQuestionBlock))
        assert len(blocks) == 1


# ---------- orchestrator round-trip -----------------------------------


def _ok():
    from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock
    return [
        AssistantMessage(content=[TextBlock(text="ok")], model="m"),
        ResultMessage(
            subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
            num_turns=1, session_id="s", total_cost_usd=0.0,
            usage={"input_tokens": 1, "output_tokens": 1}, result="ok",
        ),
    ]


def _run_hook(orch: OrchestratorSession, *, tool_use_id: str,
              tool_input: dict):
    """Invoke the orchestrator's PreToolUse hook for AskUserQuestion the
    way the SDK would. Returns the coroutine."""
    hook_input = {
        "hook_event_name": "PreToolUse",
        "tool_name": "AskUserQuestion",
        "tool_input": tool_input,
        "tool_use_id": tool_use_id,
        "session_id": "test",
        "transcript_path": "",
        "cwd": "",
    }
    return orch._ask_user_question_hook(hook_input, tool_use_id, {"signal": None})


@pytest.mark.asyncio
async def test_orchestrator_hook_intercepts_ask_user_question(
    tmp_path: Path,
):
    bus = EventBus()
    requested: list[AskUserQuestionRequested] = []
    bus.subscribe(AskUserQuestionRequested, requested.append)
    grants = PermissionGrants(cwd=tmp_path)
    manager = AgentManager(
        cwd=tmp_path, bus=bus,
        adapter_factory=lambda: FakeSDKAdapter(scripts=[_ok()]),
        permission_grants=grants,
    )
    orch = OrchestratorSession(
        cwd=tmp_path, bus=bus, manager=manager,
        adapter=FakeSDKAdapter(scripts=[_ok()]),
        permission_grants=grants,
    )
    await orch.start()

    questions = [
        {
            "question": "Which approach?",
            "multiSelect": False,
            "options": [
                {"label": "Approach A", "description": "fast"},
                {"label": "Approach B", "description": "safe"},
            ],
        },
    ]

    async def answer_after_request():
        for _ in range(100):
            await asyncio.sleep(0)
            if requested:
                break
        assert requested, "AskUserQuestionRequested was never published"
        req = requested[0]
        bus.publish(AskUserQuestionAnswered(
            agent_id="orchestrator",
            request_id=req.request_id,
            answers=(
                {"selected": ("Approach B",), "custom_text": None},
            ),
        ))

    asyncio.create_task(answer_after_request())
    result = await _run_hook(
        orch, tool_use_id="tu-1", tool_input={"questions": questions},
    )
    spec = result["hookSpecificOutput"]
    assert spec["hookEventName"] == "PreToolUse"
    assert spec["permissionDecision"] == "deny"
    reason = spec["permissionDecisionReason"]
    assert "Which approach?" in reason
    assert "Approach B" in reason
    assert "Approach A" not in reason

    assert len(requested) == 1
    assert requested[0].agent_id == "orchestrator"
    assert requested[0].tool_id == "tu-1"
    assert requested[0].questions == tuple(questions)
    await orch.stop()


@pytest.mark.asyncio
async def test_orchestrator_hook_works_in_bypass_mode(tmp_path: Path):
    """In bypass-permissions mode, the SDK skips `can_use_tool` entirely
    so the AskUserQuestion intercept lives in a PreToolUse hook. This
    test was the regression that prompted the hook design: previously
    the question auto-dismissed because `can_use_tool` never fired."""
    bus = EventBus()
    requested: list[AskUserQuestionRequested] = []
    bus.subscribe(AskUserQuestionRequested, requested.append)
    manager = AgentManager(
        cwd=tmp_path, bus=bus,
        adapter_factory=lambda: FakeSDKAdapter(scripts=[_ok()]),
    )
    # No permission_grants → bypass mode. The hook must still fire.
    orch = OrchestratorSession(
        cwd=tmp_path, bus=bus, manager=manager,
        adapter=FakeSDKAdapter(scripts=[_ok()]),
    )
    await orch.start()
    assert orch.permission_grants is None  # confirms bypass mode

    questions = [
        {
            "question": "Bypass-mode question?",
            "multiSelect": False,
            "options": [{"label": "Yes"}, {"label": "No"}],
        },
    ]

    async def answer_after_request():
        for _ in range(100):
            await asyncio.sleep(0)
            if requested:
                break
        assert requested
        req = requested[0]
        bus.publish(AskUserQuestionAnswered(
            agent_id="orchestrator",
            request_id=req.request_id,
            answers=({"selected": ("Yes",), "custom_text": None},),
        ))

    asyncio.create_task(answer_after_request())
    result = await _run_hook(
        orch, tool_use_id="tu-b", tool_input={"questions": questions},
    )
    spec = result["hookSpecificOutput"]
    assert spec["permissionDecision"] == "deny"
    assert "Bypass-mode question?" in spec["permissionDecisionReason"]
    assert "Yes" in spec["permissionDecisionReason"]
    await orch.stop()


@pytest.mark.asyncio
async def test_orchestrator_ask_user_question_rejects_empty_questions(
    tmp_path: Path,
):
    bus = EventBus()
    grants = PermissionGrants(cwd=tmp_path)
    manager = AgentManager(
        cwd=tmp_path, bus=bus,
        adapter_factory=lambda: FakeSDKAdapter(scripts=[_ok()]),
        permission_grants=grants,
    )
    orch = OrchestratorSession(
        cwd=tmp_path, bus=bus, manager=manager,
        adapter=FakeSDKAdapter(scripts=[_ok()]),
        permission_grants=grants,
    )
    await orch.start()

    result = await _run_hook(orch, tool_use_id="tu-2", tool_input={})
    spec = result["hookSpecificOutput"]
    assert spec["permissionDecision"] == "deny"
    assert "no questions" in spec["permissionDecisionReason"].lower()
    await orch.stop()


# ---------- helpers ---------------------------------------------------


def _msg(*, role: str, text: str, tool_id: str | None = None,
         tool_name: str | None = None):
    from patchfeld.events import AgentMessageAppended
    return AgentMessageAppended(
        agent_id="orchestrator", role=role, text=text,
        tool_id=tool_id, tool_name=tool_name,
    )
