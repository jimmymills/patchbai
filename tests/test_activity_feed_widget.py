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
