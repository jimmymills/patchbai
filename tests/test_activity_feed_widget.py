import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

from patchbai.activity.log import ActivityLog
from patchbai.agents.fake_sdk_adapter import FakeSDKAdapter
from patchbai.agents.manager import AgentManager
from patchbai.app import PatchbaiApp
from patchbai.events import EventBus, TabAdded
from patchbai.orchestrator.session import OrchestratorSession


def _ok():
    return [
        AssistantMessage(content=[TextBlock(text="ok")], model="fake-model"),
        ResultMessage(
            subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
            num_turns=1, session_id="fake", total_cost_usd=0.0,
            usage={"input_tokens": 1, "output_tokens": 1}, result="ok",
        ),
    ]


def _build_app(tmp_path):
    bus = EventBus()
    manager = AgentManager(
        cwd=tmp_path, bus=bus,
        adapter_factory=lambda: FakeSDKAdapter(scripts=[_ok()]),
    )
    app = PatchbaiApp(cwd=tmp_path, manager=manager, global_dir=tmp_path)
    app.event_bus = bus
    app.activity_log = ActivityLog(bus)
    app.orchestrator = OrchestratorSession(
        cwd=tmp_path, bus=bus, manager=manager,
        adapter=FakeSDKAdapter(scripts=[_ok()]),
        apply_layout=app._orchestrator_apply_layout,
        layouts_store=app.layouts_store,
        config_store=app.config_store,
        actions=app.actions_registry,
        rebind_keys=app._rebind_keys,
    )
    return app


@pytest.mark.asyncio
async def test_activity_feed_renders_backlog_on_mount(tmp_path):
    app = _build_app(tmp_path)
    # Pre-load backlog before mount.
    app.event_bus.publish(TabAdded(tab_id="t1", title="Files"))
    app.event_bus.publish(TabAdded(tab_id="t2", title="Logs"))

    async with app.run_test() as pilot:
        await pilot.pause()
        # Collect static text rows inside any ActivityFeed instance.
        from patchbai.widgets.activity_feed import ActivityFeed, _ActivityRow
        feeds = list(app.query(ActivityFeed))
        assert feeds, "default dashboard layout should mount one ActivityFeed"
        rows = list(feeds[0].query(_ActivityRow))
        # Backlog plus any default-layout-fired events. At minimum our two appear.
        labels = " ".join(r.text for r in rows)
        assert "Files" in labels
        assert "Logs" in labels


@pytest.mark.asyncio
async def test_activity_feed_appends_new_event_after_mount(tmp_path):
    app = _build_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        from patchbai.widgets.activity_feed import ActivityFeed, _ActivityRow
        feed = app.query(ActivityFeed).first()
        before = len(list(feed.query(_ActivityRow)))
        app.event_bus.publish(TabAdded(tab_id="zzz", title="Surprise"))
        await pilot.pause()
        after_rows = list(feed.query(_ActivityRow))
        assert len(after_rows) == before + 1
        assert "Surprise" in after_rows[-1].text


@pytest.mark.asyncio
async def test_mode_prop_filters_initial_render(tmp_path):
    """Layout prop `{mode: 'agents'}` should hide tab/orch/etc. kinds."""
    import json
    seed = {
        "version": 1,
        "tabs": [
            {
                "id": "main", "title": "Main",
                "layout": {
                    "version": 1,
                    "layout": {
                        "type": "horizontal",
                        "children": [
                            {"id": "orch", "widget": "OrchestratorChat", "size": "50%"},
                            {"id": "feed", "widget": "ActivityFeed",
                             "props": {"mode": "agents"}, "size": "50%"},
                        ],
                    },
                },
            },
        ],
        "active": "main",
    }
    (tmp_path / ".patchbai").mkdir()
    (tmp_path / ".patchbai" / "workspace.json").write_text(json.dumps(seed))

    app = _build_app(tmp_path)
    # Pre-load: tab.added is filtered out in agents mode.
    app.event_bus.publish(TabAdded(tab_id="t1", title="HiddenTab"))

    async with app.run_test() as pilot:
        await pilot.pause()
        from patchbai.widgets.activity_feed import ActivityFeed, _ActivityRow
        feed = app.query(ActivityFeed).first()
        rows = list(feed.query(_ActivityRow))
        labels = " ".join(r.text for r in rows)
        # tab.added is not in agents mode → "HiddenTab" must not be rendered.
        assert "HiddenTab" not in labels
        assert feed.mode == "agents"


@pytest.mark.asyncio
async def test_clicking_mode_chip_changes_mode_and_persists(tmp_path):
    import json
    seed = {
        "version": 1,
        "tabs": [
            {
                "id": "main", "title": "Main",
                "layout": {
                    "version": 1,
                    "layout": {
                        "type": "horizontal",
                        "children": [
                            {"id": "orch", "widget": "OrchestratorChat", "size": "50%"},
                            {"id": "feed", "widget": "ActivityFeed",
                             "props": {"mode": "audit"}, "size": "50%"},
                        ],
                    },
                },
            },
        ],
        "active": "main",
    }
    (tmp_path / ".patchbai").mkdir()
    (tmp_path / ".patchbai" / "workspace.json").write_text(json.dumps(seed))

    app = _build_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        from patchbai.widgets.activity_feed import ActivityFeed, _ModeChip
        feed = app.query(ActivityFeed).first()
        assert feed.mode == "audit"
        # Find the "agents" chip and click it.
        chips = list(feed.query(_ModeChip))
        agents_chip = next(c for c in chips if c.mode == "agents")
        await pilot.click(agents_chip)
        await pilot.pause()
        await pilot.pause()  # let _apply_to_tab settle
        assert feed.mode == "agents"
        # Check that workspace.json now has props.mode == "agents".
        ws_raw = json.loads((tmp_path / ".patchbai" / "workspace.json").read_text())
        children = ws_raw["tabs"][0]["layout"]["layout"]["children"]
        feed_node = next(c for c in children if c.get("widget") == "ActivityFeed")
        assert feed_node["props"]["mode"] == "agents"


@pytest.mark.asyncio
async def test_card_variant_used_for_agent_ask(tmp_path):
    app = _build_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        from patchbai.events import AgentRequestedUserInput
        app.event_bus.publish(AgentRequestedUserInput(
            agent_id="bot", question="ok?", request_id="r1",
        ))
        await pilot.pause()
        from patchbai.widgets.activity_feed import ActivityFeed, _ActivityRow
        feed = app.query(ActivityFeed).first()
        rows = list(feed.query(_ActivityRow))
        ask_row = next(r for r in rows if r.entry.kind == "agent.ask")
        assert ask_row.has_class("-variant-card")


@pytest.mark.asyncio
async def test_expanded_variant_used_for_agent_message(tmp_path):
    """agent.message is in agents/debug modes, NOT audit. Mount with mode='agents'."""
    import json
    seed = {
        "version": 1,
        "tabs": [
            {"id": "main", "title": "Main",
             "layout": {"version": 1, "layout": {
                 "type": "horizontal",
                 "children": [
                     {"id": "orch", "widget": "OrchestratorChat", "size": "50%"},
                     {"id": "feed", "widget": "ActivityFeed",
                      "props": {"mode": "agents"}, "size": "50%"},
                 ],
             }}},
        ],
        "active": "main",
    }
    (tmp_path / ".patchbai").mkdir()
    (tmp_path / ".patchbai" / "workspace.json").write_text(json.dumps(seed))
    app = _build_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        from patchbai.events import AgentMessageAppended
        app.event_bus.publish(AgentMessageAppended(
            agent_id="bot", role="assistant", text="hi",
        ))
        await pilot.pause()
        from patchbai.widgets.activity_feed import ActivityFeed, _ActivityRow
        feed = app.query(ActivityFeed).first()
        rows = list(feed.query(_ActivityRow))
        msg_row = next(r for r in rows if r.entry.kind == "agent.message")
        assert msg_row.has_class("-variant-expanded")


@pytest.mark.asyncio
async def test_compact_variant_used_for_tab_added(tmp_path):
    app = _build_app(tmp_path)
    app.event_bus.publish(TabAdded(tab_id="t1", title="Files"))
    async with app.run_test() as pilot:
        await pilot.pause()
        from patchbai.widgets.activity_feed import ActivityFeed, _ActivityRow
        feed = app.query(ActivityFeed).first()
        rows = list(feed.query(_ActivityRow))
        tab_row = next(r for r in rows if r.entry.kind == "tab.added" and r.entry.tab_id == "t1")
        assert tab_row.has_class("-variant-compact")


@pytest.mark.asyncio
async def test_clicking_agent_row_publishes_focus_request(tmp_path):
    """agent.message is in agents/debug modes; mount the feed in agents mode."""
    import json
    seed = {
        "version": 1,
        "tabs": [
            {"id": "main", "title": "Main",
             "layout": {"version": 1, "layout": {
                 "type": "horizontal",
                 "children": [
                     {"id": "orch", "widget": "OrchestratorChat", "size": "50%"},
                     {"id": "feed", "widget": "ActivityFeed",
                      "props": {"mode": "agents"}, "size": "50%"},
                 ],
             }}},
        ],
        "active": "main",
    }
    (tmp_path / ".patchbai").mkdir()
    (tmp_path / ".patchbai" / "workspace.json").write_text(json.dumps(seed))
    app = _build_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        from patchbai.events import AgentMessageAppended, AgentFocusRequested
        seen: list[AgentFocusRequested] = []
        app.event_bus.subscribe(AgentFocusRequested, lambda e: seen.append(e))
        app.event_bus.publish(AgentMessageAppended(
            agent_id="bot", role="assistant", text="hi",
        ))
        await pilot.pause()
        from patchbai.widgets.activity_feed import ActivityFeed, _ActivityRow
        feed = app.query(ActivityFeed).first()
        msg_row = next(r for r in feed.query(_ActivityRow) if r.entry.kind == "agent.message")
        await pilot.click(msg_row)
        await pilot.pause()
        assert any(e.agent_id == "bot" for e in seen)


@pytest.mark.asyncio
async def test_clicking_layout_failed_calls_notify(tmp_path):
    import json
    seed = {
        "version": 1,
        "tabs": [
            {"id": "main", "title": "Main",
             "layout": {"version": 1, "layout": {
                 "type": "horizontal",
                 "children": [
                     {"id": "orch", "widget": "OrchestratorChat", "size": "50%"},
                     {"id": "feed", "widget": "ActivityFeed", "size": "50%"},
                 ],
             }}},
        ],
        "active": "main",
    }
    (tmp_path / ".patchbai").mkdir()
    (tmp_path / ".patchbai" / "workspace.json").write_text(json.dumps(seed))
    app = _build_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        from patchbai.events import LayoutFailed
        notified: list[tuple[str, str]] = []
        original_notify = app.notify

        def _wrapped(message, **kwargs):
            notified.append((message, kwargs.get("severity", "")))
            return original_notify(message, **kwargs)

        app.notify = _wrapped  # type: ignore[assignment]
        app.event_bus.publish(LayoutFailed(error="boom", tab_id="t1"))
        await pilot.pause()
        from patchbai.widgets.activity_feed import ActivityFeed, _ActivityRow
        feed = app.query(ActivityFeed).first()
        row = next(r for r in feed.query(_ActivityRow) if r.entry.kind == "layout.failed")
        await pilot.click(row)
        await pilot.pause()
        assert any("boom" in m and sev == "error" for m, sev in notified)


@pytest.mark.asyncio
async def test_clicking_non_interactive_row_does_nothing(tmp_path):
    """workspace.cwd has no click handler; clicking it shouldn't fire any
    AgentFocusRequested or notification."""
    import json
    seed = {
        "version": 1,
        "tabs": [
            {"id": "main", "title": "Main",
             "layout": {"version": 1, "layout": {
                 "type": "horizontal",
                 "children": [
                     {"id": "orch", "widget": "OrchestratorChat", "size": "50%"},
                     {"id": "feed", "widget": "ActivityFeed", "size": "50%"},
                 ],
             }}},
        ],
        "active": "main",
    }
    (tmp_path / ".patchbai").mkdir()
    (tmp_path / ".patchbai" / "workspace.json").write_text(json.dumps(seed))
    app = _build_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        from patchbai.events import WorkspaceCwdChanged, AgentFocusRequested
        seen: list[AgentFocusRequested] = []
        app.event_bus.subscribe(AgentFocusRequested, lambda e: seen.append(e))
        app.event_bus.publish(WorkspaceCwdChanged(cwd="/tmp"))
        await pilot.pause()
        from patchbai.widgets.activity_feed import ActivityFeed, _ActivityRow
        feed = app.query(ActivityFeed).first()
        cwd_row = next(r for r in feed.query(_ActivityRow) if r.entry.kind == "workspace.cwd")
        before = len(seen)
        await pilot.click(cwd_row)
        await pilot.pause()
        assert len(seen) == before  # no new focus requests
