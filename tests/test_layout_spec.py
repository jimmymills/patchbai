import pytest

from mod_tui.layout.spec import Container, LayoutSpec, Panel


def _minimal() -> dict:
    return {
        "version": 1,
        "layout": {"id": "orch", "widget": "OrchestratorChat"},
    }


def test_minimal_spec_parses():
    spec = LayoutSpec.model_validate(_minimal())
    assert isinstance(spec.layout, Panel)
    assert spec.layout.widget == "OrchestratorChat"
    assert spec.custom_widgets == []
    assert spec.focus is None


def test_nested_container_parses():
    spec = LayoutSpec.model_validate({
        "version": 1,
        "layout": {
            "type": "horizontal",
            "children": [
                {"id": "orch", "widget": "OrchestratorChat", "size": "60%"},
                {
                    "type": "vertical",
                    "size": "40%",
                    "children": [
                        {"id": "agents", "widget": "AgentTable"},
                        {"id": "feed", "widget": "ActivityFeed"},
                    ],
                },
            ],
        },
        "focus": "orch",
    })
    root = spec.layout
    assert isinstance(root, Container) and root.type == "horizontal"
    assert len(root.children) == 2
    assert isinstance(root.children[0], Panel)
    assert isinstance(root.children[1], Container)
    assert spec.focus == "orch"


def test_spec_without_orchestrator_chat_is_rejected():
    with pytest.raises(ValueError, match="OrchestratorChat"):
        LayoutSpec.model_validate({
            "version": 1,
            "layout": {"id": "x", "widget": "AgentTable"},
        })


def test_spec_with_two_orchestrator_chats_is_rejected():
    with pytest.raises(ValueError, match="exactly one"):
        LayoutSpec.model_validate({
            "version": 1,
            "layout": {
                "type": "horizontal",
                "children": [
                    {"id": "a", "widget": "OrchestratorChat"},
                    {"id": "b", "widget": "OrchestratorChat"},
                ],
            },
        })


def test_panel_extra_fields_rejected():
    with pytest.raises(ValueError):
        LayoutSpec.model_validate({
            "version": 1,
            "layout": {"id": "orch", "widget": "OrchestratorChat", "bogus": True},
        })


def test_container_with_no_children_rejected():
    with pytest.raises(ValueError):
        LayoutSpec.model_validate({
            "version": 1,
            "layout": {"type": "horizontal", "children": []},
        })


def test_round_trip_json():
    src = LayoutSpec.model_validate(_minimal())
    dumped = src.model_dump_json()
    again = LayoutSpec.model_validate_json(dumped)
    assert again == src


def test_panel_with_explicit_props_round_trips():
    spec = LayoutSpec.model_validate({
        "version": 1,
        "layout": {
            "id": "orch",
            "widget": "OrchestratorChat",
            "props": {"placeholder": "say hi", "multiline": True},
        },
    })
    assert isinstance(spec.layout, Panel)
    assert spec.layout.props == {"placeholder": "say hi", "multiline": True}


def test_custom_widgets_non_empty_parses():
    spec = LayoutSpec.model_validate({
        "version": 1,
        "layout": {"id": "orch", "widget": "OrchestratorChat"},
        "custom_widgets": [
            {"name": "MyKanban", "source": "class MyKanban: ..."},
        ],
    })
    assert len(spec.custom_widgets) == 1
    assert spec.custom_widgets[0].name == "MyKanban"
    assert spec.custom_widgets[0].source == "class MyKanban: ..."


def test_panel_accepts_optional_title():
    panel = Panel.model_validate({"id": "feed", "widget": "ActivityFeed", "title": "Activity"})
    assert panel.title == "Activity"


def test_panel_title_defaults_to_none():
    panel = Panel.model_validate({"id": "feed", "widget": "ActivityFeed"})
    assert panel.title is None


def test_panel_title_round_trips_through_dump():
    panel = Panel.model_validate({"id": "feed", "widget": "ActivityFeed", "title": "Activity"})
    dumped = panel.model_dump(mode="json")
    assert dumped["title"] == "Activity"
    reparsed = Panel.model_validate(dumped)
    assert reparsed == panel


def test_panel_extra_fields_still_rejected_with_title_present():
    with pytest.raises(Exception):
        Panel.model_validate({"id": "feed", "widget": "ActivityFeed", "title": "Activity", "junk": 1})
