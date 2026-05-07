from pathlib import Path

import pytest

from patchbai.agents.permission_grants import PermissionGrants


def test_lookup_returns_none_when_file_missing(tmp_path: Path):
    grants = PermissionGrants(cwd=tmp_path)
    assert grants.lookup(agent_name="r", tool_name="Read") is None


def test_remember_persists_to_disk_and_round_trips(tmp_path: Path):
    grants = PermissionGrants(cwd=tmp_path)
    grants.remember(agent_name="researcher", tool_name="Read", behavior="allow")
    fresh = PermissionGrants(cwd=tmp_path)
    assert fresh.lookup(agent_name="researcher", tool_name="Read") == "allow"


def test_remember_session_only_does_not_write_disk(tmp_path: Path):
    grants = PermissionGrants(cwd=tmp_path)
    grants.remember(
        agent_name="researcher", tool_name="Read", behavior="allow",
        scope="session",
    )
    assert grants.lookup(agent_name="researcher", tool_name="Read") == "allow"
    fresh = PermissionGrants(cwd=tmp_path)
    assert fresh.lookup(agent_name="researcher", tool_name="Read") is None


def test_disk_overrides_take_precedence_over_session(tmp_path: Path):
    grants = PermissionGrants(cwd=tmp_path)
    grants.remember(agent_name="r", tool_name="Read", behavior="deny", scope="persistent")
    grants.remember(agent_name="r", tool_name="Read", behavior="allow", scope="session")
    assert grants.lookup(agent_name="r", tool_name="Read") == "deny"


def test_orchestrator_grants_round_trip(tmp_path: Path):
    # Same shape works for the orchestrator's pseudo-agent name.
    grants = PermissionGrants(cwd=tmp_path)
    grants.remember(
        agent_name="orchestrator",
        tool_name="mcp__patchbai_orchestrator__list_widgets",
        behavior="allow",
    )
    fresh = PermissionGrants(cwd=tmp_path)
    assert fresh.lookup(
        agent_name="orchestrator",
        tool_name="mcp__patchbai_orchestrator__list_widgets",
    ) == "allow"


def test_clear_wipes_disk(tmp_path: Path):
    grants = PermissionGrants(cwd=tmp_path)
    grants.remember(agent_name="r", tool_name="Read", behavior="allow")
    grants.clear()
    fresh = PermissionGrants(cwd=tmp_path)
    assert fresh.lookup(agent_name="r", tool_name="Read") is None


def test_corrupt_file_starts_empty(tmp_path: Path):
    (tmp_path / ".patchbai").mkdir()
    (tmp_path / ".patchbai" / "permission_grants.json").write_text("not json")
    grants = PermissionGrants(cwd=tmp_path)  # must not raise
    assert grants.lookup(agent_name="r", tool_name="Read") is None
