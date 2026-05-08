import time
from datetime import datetime

from patchfeld.activity.log import ActivityEntry, ActivityKind, ActivityLog
from patchfeld.agents.state import AgentInfo, AgentState
from patchfeld.events import (
    ActivityLogged, AgentArchiveChanged, AgentMessageAppended,
    AgentNotifiedOrchestrator, AgentRequestedUserInput, AgentSpawned,
    AgentStateChanged, AgentTokensTouched, EventBus, FileSelected,
    LayoutApplied, LayoutFailed, OrchestratorReply, OrchestratorSessionSwitched,
    StatsUpdated, TabAdded, TabClosed, TabSwitched, UserMessageToOrchestrator,
    WorkspaceCwdChanged,
)
from patchfeld.layout.spec import LayoutSpec


def _spec() -> LayoutSpec:
    return LayoutSpec.model_validate({
        "version": 1,
        "layout": {"id": "p", "widget": "ActivityFeed"},
    })


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


def _info(agent_id="a1", name="bot", state=AgentState.IDLE) -> AgentInfo:
    return AgentInfo(id=agent_id, name=name, cwd="/tmp", started_at=time.time(), state=state)


def test_log_captures_agent_spawned():
    bus = EventBus()
    log = ActivityLog(bus)
    bus.publish(AgentSpawned(info=_info()))
    entries = log.entries()
    assert len(entries) == 1
    assert entries[0].kind == "agent.spawned"
    assert entries[0].agent_id == "a1"
    assert "bot" in entries[0].summary


def test_log_publishes_activity_logged():
    bus = EventBus()
    log = ActivityLog(bus)
    seen: list[ActivityLogged] = []
    bus.subscribe(ActivityLogged, lambda e: seen.append(e))
    bus.publish(AgentSpawned(info=_info()))
    assert len(seen) == 1
    assert seen[0].entry is log.entries()[0]


def test_agent_state_terminal_emits_agent_done():
    bus = EventBus()
    log = ActivityLog(bus)
    bus.publish(AgentStateChanged(info=_info(state=AgentState.DONE), old_state=AgentState.RUNNING))
    assert log.entries()[0].kind == "agent.done"


def test_agent_state_non_terminal_emits_agent_state():
    bus = EventBus()
    log = ActivityLog(bus)
    bus.publish(AgentStateChanged(info=_info(state=AgentState.RUNNING), old_state=AgentState.IDLE))
    assert log.entries()[0].kind == "agent.state"


def test_agent_message_role_split():
    bus = EventBus()
    log = ActivityLog(bus)
    bus.publish(AgentMessageAppended(agent_id="a1", role="assistant", text="hi"))
    bus.publish(AgentMessageAppended(agent_id="a1", role="tool_use", text="run", tool_id="t1", tool_name="bash"))
    kinds = [e.kind for e in log.entries()]
    assert kinds == ["agent.message", "agent.tool"]


def test_agent_ask_archive_notify_captured():
    bus = EventBus()
    log = ActivityLog(bus)
    bus.publish(AgentRequestedUserInput(agent_id="a1", question="ok?", request_id="r1"))
    bus.publish(AgentNotifiedOrchestrator(agent_id="a1", message="done"))
    bus.publish(AgentArchiveChanged(info=_info()))
    kinds = [e.kind for e in log.entries()]
    assert kinds == ["agent.ask", "agent.notify", "agent.archive"]


def test_tokens_touched_and_stats_updated_are_filtered():
    bus = EventBus()
    log = ActivityLog(bus)
    bus.publish(AgentTokensTouched(agent_id="a1"))
    bus.publish(StatsUpdated(tokens_in=1, tokens_out=1, cost=0.0, active_agents=1))
    assert log.entries() == ()


def test_log_captures_orchestrator_events():
    bus = EventBus()
    log = ActivityLog(bus)
    bus.publish(UserMessageToOrchestrator(text="hi"))
    bus.publish(OrchestratorReply(text="ok"))
    bus.publish(OrchestratorSessionSwitched(session_id="s1", transcript_path="/tmp/t"))
    kinds = [e.kind for e in log.entries()]
    assert kinds == ["orch.user", "orch.reply", "orch.session"]


def test_log_captures_layout_events():
    bus = EventBus()
    log = ActivityLog(bus)
    bus.publish(LayoutApplied(spec=_spec(), layout_name="dashboard", tab_id="t1"))
    bus.publish(LayoutFailed(error="boom", tab_id="t1"))
    kinds = [e.kind for e in log.entries()]
    assert kinds == ["layout.applied", "layout.failed"]
    assert log.entries()[1].detail == "boom"


def test_log_captures_tab_events():
    bus = EventBus()
    log = ActivityLog(bus)
    bus.publish(TabAdded(tab_id="t1", title="Files"))
    bus.publish(TabClosed(tab_id="t1"))
    bus.publish(TabSwitched(tab_id="t2", title="Logs"))
    kinds = [e.kind for e in log.entries()]
    assert kinds == ["tab.added", "tab.closed", "tab.switched"]
    assert log.entries()[0].tab_id == "t1"
    assert log.entries()[2].summary == "Logs"


def test_log_captures_workspace_and_file_events():
    bus = EventBus()
    log = ActivityLog(bus)
    bus.publish(WorkspaceCwdChanged(cwd="/var/foo"))
    bus.publish(FileSelected(path="/x/y.py"))
    kinds = [e.kind for e in log.entries()]
    assert kinds == ["workspace.cwd", "file.selected"]


def test_log_evicts_when_buffer_full():
    bus = EventBus()
    log = ActivityLog(bus)
    for i in range(ActivityLog.BUFFER_SIZE + 50):
        bus.publish(TabAdded(tab_id=f"t{i}", title=f"Tab {i}"))
    entries = log.entries()
    assert len(entries) == ActivityLog.BUFFER_SIZE
    # Most recent retained.
    assert entries[-1].tab_id == f"t{ActivityLog.BUFFER_SIZE + 49}"
    # Oldest dropped.
    assert entries[0].tab_id == "t50"
