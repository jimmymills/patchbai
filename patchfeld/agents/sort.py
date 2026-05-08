"""Default sort order for the AgentTable widget.

The sort surfaces agents that need attention at the top:

  WAITING (blocked on user/orchestrator) → RUNNING/IDLE (live)
  → ERROR (triage) → DONE (terminal, least interesting)

Within a bucket, rows order by recent-activity desc, with `started_at`
asc as a stable final tiebreaker. Archived agents always sink to the
bottom regardless of state.

This is the v1 default sort. User-overridable column-click sort is
deliberately out of scope; see plan
`docs/superpowers/plans/2026-05-07-agent-table-default-sort.md`.

When `AWAITING_PERMISSION` (currently only on the approval-modals
branch) merges, add it to STATE_PRIORITY at priority 0 — same
semantic as WAITING.
"""

from __future__ import annotations

from typing import Iterable

from patchfeld.agents.state import AgentInfo, AgentState

STATE_PRIORITY: dict[AgentState, int] = {
    AgentState.WAITING: 0,
    AgentState.RUNNING: 1,
    AgentState.IDLE: 2,
    AgentState.ERROR: 3,
    AgentState.DONE: 4,
}


def _sort_key(info: AgentInfo) -> tuple:
    # Archived agents sink past every live row regardless of state.
    archived_bucket = 1 if info.archived else 0

    # Within the archived bucket, state ordering doesn't matter — every
    # archived row is "out of sight". Collapse them into a single state
    # bucket so they sort purely by ended_at desc among themselves.
    if info.archived:
        state_bucket = 0
    else:
        state_bucket = STATE_PRIORITY.get(info.state, len(STATE_PRIORITY))

    # For terminal/archived rows, prefer ended_at as the "when" timestamp;
    # last_activity is the fallback so legacy rows without ended_at still sort.
    if info.archived or info.state == AgentState.DONE:
        when = info.ended_at if info.ended_at is not None else info.last_activity
    else:
        when = info.last_activity

    # Tuple-compare: smaller archived/state buckets win; -when sorts desc;
    # started_at asc breaks ties stably.
    return (archived_bucket, state_bucket, -when, info.started_at)


def sort_agents(infos: Iterable[AgentInfo]) -> list[AgentInfo]:
    """Return `infos` ordered for default AgentTable display.

    Pure: no side effects, no Textual coupling. Caller passes whatever
    snapshot they want sorted (live, persisted, mixed).
    """
    return sorted(infos, key=_sort_key)
