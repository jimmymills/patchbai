"""Widget-level tests for Tab completion in `OrchestratorChat`'s input.

The same SlashCompleter the top `CommandBar` uses also drives Tab completion
in the orchestrator chat panel — both inputs share the host app's
`slash_completer` attribute, but each Input keeps its own per-widget cycle
state so they don't trample each other.

These tests mount only OrchestratorChat against a minimal Textual host so we
don't have to spin the full PatchfeldApp — see test_command_bar_completion.py
for the same harness pattern.
"""

from __future__ import annotations

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Input

from patchfeld.events import EventBus
from patchfeld.orchestrator.skills import SkillEntry, SkillsIndex
from patchfeld.orchestrator.slash_completion import SlashCompleter
from patchfeld.widgets.orchestrator_chat import OrchestratorChat


def _make_completer(*, builtins=("help", "reset"), skills=()) -> SlashCompleter:
    idx = SkillsIndex(entries={
        n: SkillEntry(name=n, path=f"/fake/{n}/SKILL.md", source="user")
        for n in skills
    })
    return SlashCompleter.build(builtin_commands=builtins, skills_index=idx)


class _Harness(App):
    def __init__(self, *, completer: SlashCompleter, bus: EventBus) -> None:
        super().__init__()
        self.event_bus = bus
        self.slash_completer = completer

    def compose(self) -> ComposeResult:
        yield OrchestratorChat(event_bus=self.event_bus)


@pytest.mark.asyncio
async def test_tab_on_just_slash_completes_to_first_alphabetical_match():
    completer = _make_completer(
        builtins=["help", "reset"], skills=["kb-query"],
    )
    app = _Harness(completer=completer, bus=EventBus())
    async with app.run_test() as pilot:
        await pilot.pause()
        chat_input = app.query_one(OrchestratorChat).query_one(Input)
        chat_input.focus()
        await pilot.pause()
        await pilot.press("/")
        await pilot.press("tab")
        await pilot.pause()
        # builtins+skills sorted: help, kb-query, reset
        assert chat_input.value == "/help "


@pytest.mark.asyncio
async def test_tab_cycles_through_matches():
    completer = _make_completer(
        builtins=[], skills=["alpha", "beta", "gamma"],
    )
    app = _Harness(completer=completer, bus=EventBus())
    async with app.run_test() as pilot:
        await pilot.pause()
        chat_input = app.query_one(OrchestratorChat).query_one(Input)
        chat_input.focus()
        await pilot.pause()
        await pilot.press("/")
        await pilot.press("tab")
        await pilot.pause()
        assert chat_input.value == "/alpha "
        await pilot.press("tab")
        await pilot.pause()
        assert chat_input.value == "/beta "
        await pilot.press("tab")
        await pilot.pause()
        assert chat_input.value == "/gamma "
        # Wrap around.
        await pilot.press("tab")
        await pilot.pause()
        assert chat_input.value == "/alpha "


@pytest.mark.asyncio
async def test_tab_on_non_slash_text_leaves_input_alone():
    completer = _make_completer(builtins=["help"])
    app = _Harness(completer=completer, bus=EventBus())
    async with app.run_test() as pilot:
        await pilot.pause()
        chat_input = app.query_one(OrchestratorChat).query_one(Input)
        chat_input.focus()
        await pilot.pause()
        await pilot.press(*"hello")
        await pilot.press("tab")
        await pilot.pause()
        assert chat_input.value == "hello"


@pytest.mark.asyncio
async def test_tab_after_space_in_args_leaves_input_alone():
    """`/cd /Use<Tab>` is editing an argument; v1 must NOT cycle slash
    candidates from this position."""
    completer = _make_completer(builtins=["cd", "help"])
    app = _Harness(completer=completer, bus=EventBus())
    async with app.run_test() as pilot:
        await pilot.pause()
        chat_input = app.query_one(OrchestratorChat).query_one(Input)
        chat_input.focus()
        await pilot.pause()
        await pilot.press(*"/cd /Use")
        await pilot.press("tab")
        await pilot.pause()
        assert chat_input.value == "/cd /Use"
