from datetime import datetime

from patchbai.activity.log import ActivityEntry, ActivityKind


def test_activity_entry_required_fields():
    e = ActivityEntry(
        timestamp=datetime(2026, 5, 8, 15, 42, 1),
        kind=ActivityKind.TAB_ADDED,
        summary='"Files"',
        detail=None,
        agent_id=None,
        tab_id="abc",
        raw=None,
    )
    assert e.summary == '"Files"'
    assert e.tab_id == "abc"
    assert e.kind == "tab.added"


def test_activity_kind_values_are_dotted_strings():
    # Spot-check that we expose dotted-string constants matching the spec.
    assert ActivityKind.AGENT_SPAWNED == "agent.spawned"
    assert ActivityKind.AGENT_DONE == "agent.done"
    assert ActivityKind.LAYOUT_FAILED == "layout.failed"
    assert ActivityKind.TAB_ADDED == "tab.added"
    assert ActivityKind.WORKSPACE_CWD == "workspace.cwd"
