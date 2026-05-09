"""Widget-level tests for Tab completion in `CommandBar`.

The pure matcher logic lives in `tests/test_slash_completion.py`; here we
wire a real `CommandBar` into a minimal Textual app, drive it through the
test pilot, and assert that Tab keypresses mutate the input value as
specified.

The wiring path under test:

  - the user types into the CommandBar's `Input`,
  - presses Tab (or Shift+Tab),
  - the widget consults the `SlashCompleter` it was handed at construction
    (or the one the host app exposes as `app.slash_completer`),
  - replaces the input value in place + parks the cursor at end.

Tab must NOT be globally consumed: when completion does not apply
(empty input, no leading slash, mid-argument), the keypress falls through
to Textual's default focus-traversal binding.
"""

from __future__ import annotations

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Input

from patchfeld.events import EventBus
from patchfeld.orchestrator.skills import SkillEntry, SkillsIndex
from patchfeld.orchestrator.slash_completion import SlashCompleter
from patchfeld.widgets.chrome import CommandBar


def _make_completer(*, builtins=("help", "reset"), skills=()) -> SlashCompleter:
    idx = SkillsIndex(entries={
        n: SkillEntry(name=n, path=f"/fake/{n}/SKILL.md", source="user")
        for n in skills
    })
    return SlashCompleter.build(builtin_commands=builtins, skills_index=idx)


class _Harness(App):
    """Minimal host that mounts only a CommandBar so widget tests don't have
    to spin up the whole orchestrator."""

    def __init__(self, *, completer: SlashCompleter, bus: EventBus) -> None:
        super().__init__()
        self.event_bus = bus
        self.slash_completer = completer

    def compose(self) -> ComposeResult:
        yield CommandBar(event_bus=self.event_bus, slash_completer=self.slash_completer)


@pytest.mark.asyncio
async def test_tab_on_just_slash_completes_to_first_alphabetical_match():
    """`/<Tab>` fills the input with the first command alphabetically and a
    trailing space."""
    completer = _make_completer(builtins=["help", "reset"], skills=["kb-query"])
    app = _Harness(completer=completer, bus=EventBus())
    async with app.run_test() as pilot:
        await pilot.pause()
        cmd = app.query_one(CommandBar)
        cmd.focus_input()
        await pilot.pause()
        await pilot.press("/")
        await pilot.press("tab")
        await pilot.pause()
        inp = cmd.query_one("#cmd-input", Input)
        # builtins+skills sorted: help, kb-query, reset
        assert inp.value == "/help "
        assert inp.cursor_position == len("/help ")


@pytest.mark.asyncio
async def test_tab_on_prefix_filters_then_advances():
    """`/k<Tab>` → first /k* match; second Tab cycles to the next /k* match."""
    completer = _make_completer(
        builtins=["help"], skills=["kb-query", "kbinit"],
    )
    app = _Harness(completer=completer, bus=EventBus())
    async with app.run_test() as pilot:
        await pilot.pause()
        cmd = app.query_one(CommandBar)
        cmd.focus_input()
        await pilot.pause()
        await pilot.press("/")
        await pilot.press("k")
        await pilot.press("tab")
        await pilot.pause()
        inp = cmd.query_one("#cmd-input", Input)
        assert inp.value == "/kb-query "
        await pilot.press("tab")
        await pilot.pause()
        assert inp.value == "/kbinit "


@pytest.mark.asyncio
async def test_tab_on_non_slash_text_leaves_input_alone():
    """Without a leading slash the binding must fall through — Tab acts as
    Textual's default focus traversal, so the input value is unchanged."""
    completer = _make_completer(builtins=["help"])
    app = _Harness(completer=completer, bus=EventBus())
    async with app.run_test() as pilot:
        await pilot.pause()
        cmd = app.query_one(CommandBar)
        cmd.focus_input()
        await pilot.pause()
        await pilot.press(*"hello")
        await pilot.press("tab")
        await pilot.pause()
        inp = cmd.query_one("#cmd-input", Input)
        assert inp.value == "hello"


@pytest.mark.asyncio
async def test_tab_after_space_in_command_args_leaves_input_alone():
    """`/cd /Use<Tab>` is in the middle of an argument — completion must NOT
    cycle slash candidates. Path completion is out of scope for v1."""
    completer = _make_completer(builtins=["cd", "help"])
    app = _Harness(completer=completer, bus=EventBus())
    async with app.run_test() as pilot:
        await pilot.pause()
        cmd = app.query_one(CommandBar)
        cmd.focus_input()
        await pilot.pause()
        await pilot.press(*"/cd /Use")
        await pilot.press("tab")
        await pilot.pause()
        inp = cmd.query_one("#cmd-input", Input)
        assert inp.value == "/cd /Use"


@pytest.mark.asyncio
async def test_typing_resets_cycle_state():
    """After completing once, typing additional chars must wipe the cycle
    anchor — the next Tab starts a fresh cycle from the new prefix."""
    completer = _make_completer(
        builtins=[], skills=["kb-query", "kbinit", "garden"],
    )
    app = _Harness(completer=completer, bus=EventBus())
    async with app.run_test() as pilot:
        await pilot.pause()
        cmd = app.query_one(CommandBar)
        cmd.focus_input()
        await pilot.pause()
        await pilot.press("/")
        await pilot.press("k")
        await pilot.press("tab")  # → /kb-query
        await pilot.pause()
        inp = cmd.query_one("#cmd-input", Input)
        assert inp.value == "/kb-query "
        # User edits the value — clear and retype "/g".
        inp.value = "/g"
        inp.cursor_position = len(inp.value)
        await pilot.pause()
        await pilot.press("tab")
        await pilot.pause()
        assert inp.value == "/garden "


@pytest.mark.asyncio
async def test_shift_tab_cycles_backward():
    """Shift+Tab steps backward through matches: a fresh Shift+Tab on `/`
    jumps to the alphabetically last command."""
    completer = _make_completer(
        builtins=[], skills=["alpha", "beta", "gamma"],
    )
    app = _Harness(completer=completer, bus=EventBus())
    async with app.run_test() as pilot:
        await pilot.pause()
        cmd = app.query_one(CommandBar)
        cmd.focus_input()
        await pilot.pause()
        await pilot.press("/")
        await pilot.press("shift+tab")
        await pilot.pause()
        inp = cmd.query_one("#cmd-input", Input)
        assert inp.value == "/gamma "
