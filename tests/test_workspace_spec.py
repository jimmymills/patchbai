import pytest

from mod_tui.layout.spec import LayoutSpec, Tabs
from mod_tui.workspace.spec import Workspace


def _layout_with_chat(panel_id: str = "orch") -> dict:
    return {
        "version": 1,
        "layout": {"id": panel_id, "widget": "OrchestratorChat"},
    }


def _layout_chatless() -> dict:
    return {
        "version": 1,
        "layout": {"id": "feed", "widget": "ActivityFeed"},
    }


def test_workspace_with_one_chat_tab_parses():
    ws = Workspace.model_validate({
        "version": 1,
        "tabs": [
            {"id": "t1", "title": "Main", "layout": _layout_with_chat()},
        ],
        "active": "t1",
    })
    assert ws.active == "t1"
    assert len(ws.tabs) == 1
    assert isinstance(ws.tabs[0].layout, LayoutSpec)


def test_workspace_with_chatless_tab_only_is_rejected():
    with pytest.raises(ValueError, match="at least one OrchestratorChat"):
        Workspace.model_validate({
            "version": 1,
            "tabs": [
                {"id": "t1", "title": "Logs", "layout": _layout_chatless()},
            ],
            "active": "t1",
        })


def test_workspace_with_chat_in_one_of_many_tabs_is_accepted():
    ws = Workspace.model_validate({
        "version": 1,
        "tabs": [
            {"id": "t1", "title": "Logs", "layout": _layout_chatless()},
            {"id": "t2", "title": "Main", "layout": _layout_with_chat()},
        ],
        "active": "t2",
    })
    assert {t.id for t in ws.tabs} == {"t1", "t2"}


def test_workspace_active_must_reference_existing_tab():
    with pytest.raises(ValueError, match="active tab id"):
        Workspace.model_validate({
            "version": 1,
            "tabs": [
                {"id": "t1", "title": "Main", "layout": _layout_with_chat()},
            ],
            "active": "ghost",
        })


def test_workspace_rejects_empty_tabs():
    with pytest.raises(ValueError):
        Workspace.model_validate({"version": 1, "tabs": [], "active": "x"})


def test_workspace_rejects_duplicate_tab_ids():
    with pytest.raises(ValueError, match="duplicate tab id"):
        Workspace.model_validate({
            "version": 1,
            "tabs": [
                {"id": "t1", "title": "A", "layout": _layout_with_chat("a")},
                {"id": "t1", "title": "B", "layout": _layout_chatless()},
            ],
            "active": "t1",
        })


def test_workspace_round_trips_through_json():
    ws = Workspace.model_validate({
        "version": 1,
        "tabs": [
            {"id": "t1", "title": "Main", "layout": _layout_with_chat()},
        ],
        "active": "t1",
    })
    again = Workspace.model_validate_json(ws.model_dump_json())
    assert again == ws


def test_workspace_chat_in_panel_tabs_node_counts():
    # OrchestratorChat hidden inside a panel-level Tabs node still satisfies
    # the workspace invariant (we walk Tabs.children too).
    ws = Workspace.model_validate({
        "version": 1,
        "tabs": [{
            "id": "t1",
            "title": "Main",
            "layout": {
                "version": 1,
                "layout": {
                    "type": "tabs",
                    "children": [
                        {"id": "orch", "widget": "OrchestratorChat"},
                        {"id": "feed", "widget": "ActivityFeed"},
                    ],
                },
            },
        }],
        "active": "t1",
    })
    tabs_node = ws.tabs[0].layout.layout
    assert isinstance(tabs_node, Tabs)
    assert tabs_node.children[0].widget == "OrchestratorChat"


def test_workspace_chat_inside_container_wrapping_tabs_counts():
    # Path Container → Tabs → Panel(OrchestratorChat). _contains_chat must
    # recurse through Container and into Tabs.children.
    ws = Workspace.model_validate({
        "version": 1,
        "tabs": [{
            "id": "t1",
            "title": "Main",
            "layout": {
                "version": 1,
                "layout": {
                    "type": "horizontal",
                    "children": [
                        {
                            "type": "tabs",
                            "children": [
                                {"id": "orch", "widget": "OrchestratorChat"},
                            ],
                        },
                    ],
                },
            },
        }],
        "active": "t1",
    })
    assert len(ws.tabs) == 1


def test_workspace_active_theme_defaults_to_none():
    ws = Workspace.model_validate({
        "version": 1,
        "tabs": [
            {"id": "t1", "title": "Main", "layout": _layout_with_chat()},
        ],
        "active": "t1",
    })
    assert ws.active_theme is None


def test_workspace_active_theme_round_trips():
    ws = Workspace.model_validate({
        "version": 1,
        "tabs": [
            {"id": "t1", "title": "Main", "layout": _layout_with_chat()},
        ],
        "active": "t1",
        "active_theme": "nord",
    })
    assert ws.active_theme == "nord"
    assert ws.model_dump(mode="json")["active_theme"] == "nord"
