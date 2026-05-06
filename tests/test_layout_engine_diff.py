from mod_tui.layout.defaults import dashboard_layout
from mod_tui.layout.engine import (
    MountPanel,
    UnmountPanel,
    UpdateProps,
    diff,
)
from mod_tui.layout.spec import LayoutSpec


def _spec(panels: list[dict]) -> LayoutSpec:
    return LayoutSpec.model_validate({
        "version": 1,
        "layout": {"type": "horizontal", "children": panels},
    })


def test_initial_diff_mounts_everything():
    new = dashboard_layout()
    ops = diff(None, new)
    mounts = [op for op in ops if isinstance(op, MountPanel)]
    assert {op.panel.id for op in mounts} == {"orch", "agents", "feed"}


def test_no_change_produces_no_ops():
    spec = dashboard_layout()
    assert diff(spec, spec) == []


def test_changed_props_produces_update():
    a = _spec([
        {"id": "orch", "widget": "OrchestratorChat", "props": {"x": 1}},
    ])
    b = _spec([
        {"id": "orch", "widget": "OrchestratorChat", "props": {"x": 2}},
    ])
    ops = diff(a, b)
    assert ops == [UpdateProps(panel_id="orch", props={"x": 2})]


def test_changed_widget_type_unmounts_then_mounts():
    a = _spec([
        {"id": "orch", "widget": "OrchestratorChat"},
        {"id": "x", "widget": "AgentTable"},
    ])
    b = _spec([
        {"id": "orch", "widget": "OrchestratorChat"},
        {"id": "x", "widget": "ActivityFeed"},
    ])
    ops = diff(a, b)
    kinds = [type(op).__name__ for op in ops]
    assert "UnmountPanel" in kinds and "MountPanel" in kinds


def test_removed_panel_is_unmounted():
    a = _spec([
        {"id": "orch", "widget": "OrchestratorChat"},
        {"id": "x", "widget": "AgentTable"},
    ])
    b = _spec([{"id": "orch", "widget": "OrchestratorChat"}])
    ops = diff(a, b)
    assert any(isinstance(op, UnmountPanel) and op.panel_id == "x" for op in ops)


def test_added_panel_is_mounted():
    a = _spec([{"id": "orch", "widget": "OrchestratorChat"}])
    b = _spec([
        {"id": "orch", "widget": "OrchestratorChat"},
        {"id": "agents", "widget": "AgentTable"},
    ])
    ops = diff(a, b)
    assert any(isinstance(op, MountPanel) and op.panel.id == "agents" for op in ops)
