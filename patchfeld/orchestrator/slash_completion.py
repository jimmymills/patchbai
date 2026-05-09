"""Tab completion for `/`-prefixed slash commands in chat input boxes.

Both the top `CommandBar` and the `OrchestratorChat` input use this helper to
turn a Tab keypress into an in-place completion against the union of:

  - the orchestrator's built-in slash commands (`/help`, `/cd`, ...) — i.e.
    the same names that drive `_BUILTIN_COMMAND_NAMES` in
    `patchfeld.orchestrator.session`, AND
  - every locally-installed skill discovered by
    `patchfeld.orchestrator.skills.discover_skills`.

The candidate set is a snapshot taken at orchestrator-session-start (matching
how the slash-dispatch path freezes the same set), so the same `SkillsIndex`
is reused — discovery is *not* duplicated.

Behaviour (v1):

  - First Tab on `/<prefix>` completes to the first alphabetical match plus
    a single trailing space; cursor goes to end-of-text.
  - Repeated Tabs without intervening edits cycle through subsequent matches
    (Shift+Tab cycles backward). After the last, wrap.
  - Any text edit (typing or Backspace) breaks the cycle anchor — the next
    Tab starts a fresh cycle from the new prefix.
  - No completion is offered when the input does not start with `/`, is
    empty, or contains a space outside an active cycle (so `/cd /Use<Tab>`
    falls through to Textual's default Tab focus traversal — path
    completion is out of scope).
  - Cycle state is per-widget (keyed by an opaque string) so two inputs do
    not interfere.

No dropdown UI in v1; the input value just changes in place.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from textual.suggester import Suggester

from patchfeld.orchestrator.skills import SkillsIndex


@dataclass(frozen=True)
class CompletionResult:
    """Outcome of a single Tab press the input widget should apply.

    Attributes
    ----------
    text:
        The full new value of the input box (already includes leading slash
        and trailing space).
    cursor:
        Position to move the input cursor to. Always end-of-text in v1.
    """

    text: str
    cursor: int


@dataclass
class SlashCompleter:
    """Per-session completion engine.

    Construct via :meth:`build` so the candidate list is computed once,
    sorted, and deduped. The instance is shared across input widgets — cycle
    state is partitioned by `key` (typically the Input widget's id).
    """

    _candidates: tuple[str, ...]
    """Bare command names (without leading slash), sorted alphabetically."""

    _cycle_state: dict[str, dict] = field(default_factory=dict)
    """Per-key snapshot of the active cycle.

    Shape: `{key: {"matches": [...], "index": int, "last_set": str}}`. The
    `last_set` value is the full text we last wrote into the widget — when
    the next Tab arrives we compare to detect "user did not edit between
    presses", which is what licences cycling instead of restarting.
    """

    @classmethod
    def build(
        cls,
        *,
        builtin_commands: Iterable[str],
        skills_index: SkillsIndex | None = None,
    ) -> "SlashCompleter":
        """Snapshot the candidate set.

        Names from `builtin_commands` and `skills_index.names()` are merged
        (set-union for dedupe) and sorted alphabetically. Pass an empty
        index for tests/headless callers.
        """
        names: set[str] = set(builtin_commands)
        if skills_index is not None:
            names.update(skills_index.names())
        return cls(_candidates=tuple(sorted(names)))

    # --- read-only inspection --------------------------------------------

    def candidates(self) -> tuple[str, ...]:
        """Bare names, sorted. Useful for diagnostics; widgets call `match`
        or `cycle` instead."""
        return self._candidates

    # --- pure prefix matching --------------------------------------------

    def match(self, prefix: str) -> list[str]:
        """Return commands (with leading slash) whose name starts with
        `prefix`, case-insensitively. `prefix` must include the leading
        slash; pass `/` to retrieve every candidate.

        Returns `[]` if `prefix` does not start with `/` so callers can
        delegate the "is this even a slash" check to one place.
        """
        if not prefix.startswith("/"):
            return []
        pfx_lower = prefix.lower()
        return [
            f"/{name}"
            for name in self._candidates
            if f"/{name}".lower().startswith(pfx_lower)
        ]

    # --- cycle bookkeeping -----------------------------------------------

    def reset(self, key: str) -> None:
        """Forget any cycle state for `key`. Inputs call this on edit
        events (typing, Backspace, value programmatically cleared) so the
        next Tab starts fresh."""
        self._cycle_state.pop(key, None)

    @staticmethod
    def _is_fresh_trigger(text: str) -> bool:
        """A Tab press starts a fresh cycle only when the value looks like
        a single command word: leading `/`, no whitespace anywhere. Empty
        strings and free-form prose return False so the binding falls
        through to Textual's default Tab focus traversal."""
        if not text.startswith("/"):
            return False
        # Any whitespace (including the trailing-space we ourselves write
        # after a completion) disqualifies a fresh trigger; the cycle path
        # below recognises our own writes via `last_set` so the user can
        # still continue an in-flight cycle.
        return not any(ch.isspace() for ch in text)

    def cycle(
        self,
        *,
        key: str,
        current_text: str,
        direction: int = 1,
    ) -> CompletionResult | None:
        """Compute the next completion for input identified by `key`.

        Returns ``None`` when the keypress should fall through to Textual's
        default Tab handling — the widget reads ``None`` as "I'm not
        consuming this Tab".

        Parameters
        ----------
        key:
            Stable identifier for the input widget (typically its DOM id).
        current_text:
            The input's current value.
        direction:
            ``+1`` to advance through matches (Tab); ``-1`` to step back
            (Shift+Tab).
        """
        # --- continuing an in-flight cycle? ---
        state = self._cycle_state.get(key)
        if state is not None and state.get("last_set") == current_text:
            matches: list[str] = state["matches"]
            if not matches:
                return None
            new_idx = (state["index"] + direction) % len(matches)
            return self._record_and_emit(key, matches, new_idx)

        # --- otherwise, must look like a fresh `/<word>` trigger ---
        if not self._is_fresh_trigger(current_text):
            self._cycle_state.pop(key, None)
            return None

        matches = self.match(current_text)
        if not matches:
            self._cycle_state.pop(key, None)
            return None

        # Forward starts at index 0; backward starts at the last match so
        # Shift+Tab on a fresh trigger jumps to the alphabetical tail (the
        # natural inverse of forward-from-zero).
        start_idx = 0 if direction >= 0 else len(matches) - 1
        return self._record_and_emit(key, matches, start_idx)

    def _record_and_emit(
        self, key: str, matches: list[str], idx: int,
    ) -> CompletionResult:
        """Persist cycle state and return the rendered completion. Always
        appends a single trailing space — every slash command can take args,
        so the unconditional space is the cheapest right-default."""
        chosen = matches[idx]
        new_text = chosen + " "
        self._cycle_state[key] = {
            "matches": matches,
            "index": idx,
            "last_set": new_text,
        }
        return CompletionResult(text=new_text, cursor=len(new_text))


class SlashSuggester(Suggester):
    """Textual `Suggester` adapter exposing a `SlashCompleter`'s first
    match as in-input ghost text.

    The Textual `Input` widget polls a `Suggester` whenever its value
    changes; if `get_suggestion(value)` returns a string that has `value`
    as a (case-insensitive) prefix, Input renders the suffix in a faded
    color after the cursor — the user sees what Tab is about to fill.

    We share `SlashCompleter` rather than carry our own candidate set so
    the preview cannot drift from the Tab-completion behaviour: identical
    trigger predicate, identical match function, identical sort order →
    the previewed command is always the very command Tab will fill.
    """

    def __init__(self, completer: SlashCompleter) -> None:
        # use_cache=False because the underlying SkillsIndex is already a
        # cheap dict lookup — caching adds nothing — and disabling it
        # avoids surprising staleness if anyone ever swaps the completer
        # at runtime. case_sensitive=False so the user sees a sensible
        # preview when they type `/K` and the canonical command is
        # lowercase (`/kb-query`).
        super().__init__(use_cache=False, case_sensitive=False)
        self._completer = completer

    async def get_suggestion(self, value: str) -> str | None:
        # Re-use the same trigger predicate that gates the Tab handler so
        # the preview can never appear in positions where Tab is a no-op
        # (mid-argument, no leading slash, trailing space after a fresh
        # completion, …).
        if not SlashCompleter._is_fresh_trigger(value):
            return None
        matches = self._completer.match(value)
        if not matches:
            return None
        return matches[0]
