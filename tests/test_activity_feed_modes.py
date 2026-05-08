import pytest

from patchfeld.widgets.activity_feed import _MODE_KINDS, MODES


# Source-of-truth coverage table from the design spec. Each row: (kind, modes-where-visible).
_TABLE = [
    ("agent.spawned",  {"audit", "agents", "debug"}),
    ("agent.state",    {"audit", "agents", "debug"}),
    ("agent.done",     {"audit", "agents", "notifs", "debug"}),
    ("agent.message",  {"agents", "debug"}),
    ("agent.tool",     {"debug"}),
    ("agent.ask",      {"audit", "agents", "notifs", "debug"}),
    ("agent.notify",   {"audit", "agents", "notifs", "debug"}),
    ("agent.archive",  {"audit", "agents", "debug"}),
    ("orch.user",      {"audit", "debug"}),
    ("orch.reply",     {"audit", "debug"}),
    ("orch.session",   {"audit", "debug"}),
    ("layout.applied", {"audit", "debug"}),
    ("layout.failed",  {"audit", "notifs", "debug"}),
    ("tab.added",      {"audit", "debug"}),
    ("tab.closed",     {"audit", "debug"}),
    ("tab.switched",   {"debug"}),
    ("workspace.cwd",  {"audit", "notifs", "debug"}),
    ("file.selected",  {"debug"}),
]


@pytest.mark.parametrize("kind,visible_modes", _TABLE)
def test_mode_filter_matches_spec_table(kind: str, visible_modes: set[str]):
    for mode in MODES:
        actually_visible = kind in _MODE_KINDS[mode]
        expected_visible = mode in visible_modes
        assert actually_visible == expected_visible, (
            f"kind={kind!r} mode={mode!r}: "
            f"expected_visible={expected_visible}, actually_visible={actually_visible}"
        )


def test_modes_constant_exposes_all_four():
    assert MODES == ("audit", "agents", "notifs", "debug")
