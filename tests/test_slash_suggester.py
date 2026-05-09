"""Tests for SlashSuggester — Textual `Suggester` adapter that paints
greyed-out ghost text in the chat-input boxes.

The suggester reuses the same `SlashCompleter.match` logic the Tab handler
uses, so the preview is always the command Tab would actually fill (the
first alphabetical match for the typed prefix). When completion does NOT
apply (no leading slash, mid-argument, no matches), the suggester returns
None and Textual paints nothing — matching the Tab handler's no-op rule.

Also includes widget tests proving that both `CommandBar` and
`OrchestratorChat` actually wire the suggester onto their `Input` so
production users see the preview.
"""

from __future__ import annotations

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Input

from patchfeld.events import EventBus
from patchfeld.orchestrator.skills import SkillEntry, SkillsIndex
from patchfeld.orchestrator.slash_completion import SlashCompleter, SlashSuggester
from patchfeld.widgets.chrome import CommandBar
from patchfeld.widgets.orchestrator_chat import OrchestratorChat


def _make_completer(*, builtins=("help", "reset"), skills=()) -> SlashCompleter:
    idx = SkillsIndex(entries={
        n: SkillEntry(name=n, path=f"/fake/{n}/SKILL.md", source="user")
        for n in skills
    })
    return SlashCompleter.build(builtin_commands=builtins, skills_index=idx)


# ---------------------------------------------------------------------------
# Pure suggester behaviour
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_suggests_first_alphabetical_match_for_prefix():
    s = SlashSuggester(_make_completer(
        builtins=[], skills=["kb-query", "kbinit"],
    ))
    assert await s.get_suggestion("/k") == "/kb-query"


@pytest.mark.asyncio
async def test_suggests_first_command_for_bare_slash():
    s = SlashSuggester(_make_completer(
        builtins=["help", "reset"], skills=[],
    ))
    assert await s.get_suggestion("/") == "/help"


@pytest.mark.asyncio
async def test_returns_none_for_non_slash_text():
    s = SlashSuggester(_make_completer(builtins=["help"]))
    assert await s.get_suggestion("hello") is None


@pytest.mark.asyncio
async def test_returns_none_for_empty_value():
    s = SlashSuggester(_make_completer(builtins=["help"]))
    assert await s.get_suggestion("") is None


@pytest.mark.asyncio
async def test_returns_none_when_value_contains_whitespace():
    """`/cd /Use` is mid-argument — no preview, same rule as Tab."""
    s = SlashSuggester(_make_completer(builtins=["cd", "help"]))
    assert await s.get_suggestion("/cd /Use") is None


@pytest.mark.asyncio
async def test_returns_none_when_value_has_trailing_space():
    """A just-completed value (`/help `) has a trailing space; Tab cycles
    in this state but the suggester goes silent — there's no ghost preview
    to paint when the visible text already shows the full command."""
    s = SlashSuggester(_make_completer(builtins=["help"]))
    assert await s.get_suggestion("/help ") is None


@pytest.mark.asyncio
async def test_returns_none_when_no_matches():
    s = SlashSuggester(_make_completer(builtins=["help"]))
    assert await s.get_suggestion("/zzz") is None


# ---------------------------------------------------------------------------
# Widget wiring
# ---------------------------------------------------------------------------

class _CmdHarness(App):
    def __init__(self, *, completer: SlashCompleter, bus: EventBus) -> None:
        super().__init__()
        self.event_bus = bus
        self.slash_completer = completer

    def compose(self) -> ComposeResult:
        yield CommandBar(event_bus=self.event_bus, slash_completer=self.slash_completer)


class _ChatHarness(App):
    def __init__(self, *, completer: SlashCompleter, bus: EventBus) -> None:
        super().__init__()
        self.event_bus = bus
        self.slash_completer = completer

    def compose(self) -> ComposeResult:
        yield OrchestratorChat(event_bus=self.event_bus)


@pytest.mark.asyncio
async def test_command_bar_input_has_slash_suggester_after_mount():
    """The Input inside CommandBar must have a SlashSuggester attached so
    Textual paints ghost text for `/<prefix>` values."""
    completer = _make_completer(builtins=["help"], skills=["kb-query"])
    app = _CmdHarness(completer=completer, bus=EventBus())
    async with app.run_test() as pilot:
        await pilot.pause()
        inp = app.query_one(CommandBar).query_one("#cmd-input", Input)
        assert isinstance(inp.suggester, SlashSuggester)
        # The wired suggester returns the first /k* match for "/k".
        assert await inp.suggester.get_suggestion("/k") == "/kb-query"


@pytest.mark.asyncio
async def test_orchestrator_chat_input_has_slash_suggester_after_mount():
    completer = _make_completer(builtins=["help"], skills=["kb-query"])
    app = _ChatHarness(completer=completer, bus=EventBus())
    async with app.run_test() as pilot:
        await pilot.pause()
        chat = app.query_one(OrchestratorChat)
        inp = chat.query_one("#orch-input", Input)
        assert isinstance(inp.suggester, SlashSuggester)
        assert await inp.suggester.get_suggestion("/h") == "/help"
