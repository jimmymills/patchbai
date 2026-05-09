"""Tests for SlashCompleter — Tab-completion for `/`-prefixed commands.

The completer is a pure-Python helper used by both `CommandBar` and
`OrchestratorChat`. It pulls its candidate set from the same skills index the
orchestrator's slash-dispatch uses (see `patchfeld.orchestrator.skills`) plus
the orchestrator's built-in command names — the discovery mechanism is
shared, not duplicated.

Behavior covered here:

- Pure-function matching (`match`) for prefix filtering, sort order,
  case-insensitivity, and dedupe across builtins + skills.
- Cycle behavior (`cycle`) for first-press, second-press advance, wrap,
  reverse, no-match, reset-on-text-change, and per-widget state isolation.
"""

from __future__ import annotations

from patchfeld.orchestrator.skills import SkillEntry, SkillsIndex
from patchfeld.orchestrator.slash_completion import SlashCompleter


def _make_skills(*names: str) -> SkillsIndex:
    """Build a SkillsIndex from bare names without touching the filesystem."""
    return SkillsIndex(entries={
        n: SkillEntry(name=n, path=f"/fake/{n}/SKILL.md", source="user")
        for n in names
    })


# ---------------------------------------------------------------------------
# match() — pure prefix filtering
# ---------------------------------------------------------------------------

def test_match_returns_full_list_for_bare_slash():
    """`/<Tab>` must match every available command, sorted alphabetically."""
    c = SlashCompleter.build(
        builtin_commands=["help", "reset"],
        skills_index=_make_skills("kb-query", "garden"),
    )
    assert c.match("/") == ["/garden", "/help", "/kb-query", "/reset"]


def test_match_filters_by_prefix():
    c = SlashCompleter.build(
        builtin_commands=["help", "reset"],
        skills_index=_make_skills("kb-query", "kbinit", "garden"),
    )
    assert c.match("/k") == ["/kb-query", "/kbinit"]


def test_match_returns_empty_when_nothing_starts_with_prefix():
    c = SlashCompleter.build(
        builtin_commands=["help"],
        skills_index=_make_skills("kb-query"),
    )
    assert c.match("/zzz") == []


def test_match_is_case_insensitive():
    """Typing `/K` should still find `/kb-query`."""
    c = SlashCompleter.build(
        builtin_commands=["help"],
        skills_index=_make_skills("kb-query"),
    )
    assert c.match("/K") == ["/kb-query"]


def test_match_dedupes_builtin_and_skill_with_same_name():
    """A skill that collides with a built-in name appears once in the
    candidate list — exact dispatch precedence is decided elsewhere; the
    completer just doesn't show the entry twice."""
    c = SlashCompleter.build(
        builtin_commands=["help"],
        skills_index=_make_skills("help", "kb-query"),
    )
    assert c.match("/") == ["/help", "/kb-query"]


def test_match_returns_empty_when_prefix_lacks_leading_slash():
    c = SlashCompleter.build(
        builtin_commands=["help"], skills_index=None,
    )
    assert c.match("help") == []


def test_match_handles_empty_candidate_set():
    c = SlashCompleter.build(builtin_commands=[], skills_index=None)
    assert c.match("/") == []


# ---------------------------------------------------------------------------
# cycle() — what the widget actually consumes
# ---------------------------------------------------------------------------

def test_cycle_first_press_completes_to_first_match_with_trailing_space():
    """First Tab fills the first alphabetical match plus a single space; the
    cursor moves to the end of the text. Every slash command may take args,
    so the trailing space is unconditional."""
    c = SlashCompleter.build(
        builtin_commands=["help"],
        skills_index=_make_skills("kb-query", "kbinit"),
    )
    result = c.cycle(key="cmd", current_text="/k")
    assert result is not None
    assert result.text == "/kb-query "
    assert result.cursor == len("/kb-query ")


def test_cycle_second_press_advances_to_next_match():
    """Without retyping in between, a second Tab must move to the next
    candidate alphabetically."""
    c = SlashCompleter.build(
        builtin_commands=[],
        skills_index=_make_skills("kb-query", "kbinit"),
    )
    first = c.cycle(key="cmd", current_text="/k")
    assert first is not None and first.text == "/kb-query "
    second = c.cycle(key="cmd", current_text=first.text)
    assert second is not None and second.text == "/kbinit "


def test_cycle_wraps_around_at_end():
    """After the final match, another Tab returns to the first."""
    c = SlashCompleter.build(
        builtin_commands=[],
        skills_index=_make_skills("alpha", "beta"),
    )
    r1 = c.cycle(key="cmd", current_text="/")
    r2 = c.cycle(key="cmd", current_text=r1.text if r1 else "")  # type: ignore[arg-type]
    r3 = c.cycle(key="cmd", current_text=r2.text if r2 else "")  # type: ignore[arg-type]
    assert r1 is not None and r1.text == "/alpha "
    assert r2 is not None and r2.text == "/beta "
    assert r3 is not None and r3.text == "/alpha "


def test_cycle_reverse_direction_steps_backward():
    c = SlashCompleter.build(
        builtin_commands=[],
        skills_index=_make_skills("alpha", "beta", "gamma"),
    )
    # Fresh cycle backward starts at the LAST match.
    r1 = c.cycle(key="cmd", current_text="/", direction=-1)
    assert r1 is not None and r1.text == "/gamma "
    r2 = c.cycle(key="cmd", current_text=r1.text, direction=-1)
    assert r2 is not None and r2.text == "/beta "


def test_cycle_returns_none_when_no_match():
    c = SlashCompleter.build(
        builtin_commands=["help"], skills_index=None,
    )
    assert c.cycle(key="cmd", current_text="/zzz") is None


def test_cycle_returns_none_when_text_does_not_start_with_slash():
    """Non-slash text must fall through to Textual's default Tab behavior —
    the widget reads None and lets the binding propagate."""
    c = SlashCompleter.build(
        builtin_commands=["help"], skills_index=None,
    )
    assert c.cycle(key="cmd", current_text="hello") is None
    assert c.cycle(key="cmd", current_text="") is None


def test_cycle_falls_through_when_text_contains_a_space_outside_a_running_cycle():
    """`/cd /Use<Tab>` must NOT cycle slash candidates — the cursor is past the
    first space, so the user is editing an argument, not the command word.
    Path completion is deliberately out of scope for v1."""
    c = SlashCompleter.build(
        builtin_commands=["cd"],
        skills_index=_make_skills("kb-query"),
    )
    assert c.cycle(key="cmd", current_text="/cd /Use") is None


def test_cycle_resets_when_user_modifies_text():
    """Once the user types or backspaces, the cycle anchor is broken — the
    next Tab starts a brand-new cycle from the new prefix."""
    c = SlashCompleter.build(
        builtin_commands=[],
        skills_index=_make_skills("kb-query", "kbinit", "garden"),
    )
    r1 = c.cycle(key="cmd", current_text="/k")  # → "/kb-query "
    assert r1 is not None and r1.text == "/kb-query "
    # User retypes — say they cleared and typed "/g".
    r2 = c.cycle(key="cmd", current_text="/g")
    assert r2 is not None
    assert r2.text == "/garden "  # fresh start from new prefix


def test_cycle_state_is_per_widget_key():
    """Two inputs cycling concurrently must not share state."""
    c = SlashCompleter.build(
        builtin_commands=[],
        skills_index=_make_skills("alpha", "beta", "gamma"),
    )
    a1 = c.cycle(key="A", current_text="/")
    b1 = c.cycle(key="B", current_text="/")
    assert a1 is not None and a1.text == "/alpha "
    assert b1 is not None and b1.text == "/alpha "
    # Advance A only.
    a2 = c.cycle(key="A", current_text=a1.text)
    assert a2 is not None and a2.text == "/beta "
    # B's cycle is untouched — feeding its own last_set still steps from index 0.
    b2 = c.cycle(key="B", current_text=b1.text)
    assert b2 is not None and b2.text == "/beta "


def test_cycle_handles_just_slash_with_single_match():
    """A single match still appends a space and returns the same value on
    every subsequent Tab (cycle of length 1 wraps to itself)."""
    c = SlashCompleter.build(
        builtin_commands=[],
        skills_index=_make_skills("only-one"),
    )
    r1 = c.cycle(key="cmd", current_text="/")
    assert r1 is not None and r1.text == "/only-one "
    r2 = c.cycle(key="cmd", current_text=r1.text)
    assert r2 is not None and r2.text == "/only-one "
