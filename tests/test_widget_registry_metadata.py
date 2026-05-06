import pytest
from textual.widget import Widget

from mod_tui.layout.registry import WidgetRegistry


class _W(Widget):
    pass


def test_register_with_description_and_schema_then_list():
    reg = WidgetRegistry()
    reg.register(
        "MyWidget", _W,
        description="does the thing",
        props_schema={"file_path": str},
    )
    info = reg.describe("MyWidget")
    assert info.name == "MyWidget"
    assert info.cls is _W
    assert info.description == "does the thing"
    assert info.props_schema == {"file_path": str}


def test_register_without_metadata_uses_defaults():
    reg = WidgetRegistry()
    reg.register("Plain", _W)
    info = reg.describe("Plain")
    assert info.description == ""
    assert info.props_schema == {}


def test_describe_unknown_raises():
    reg = WidgetRegistry()
    with pytest.raises(KeyError):
        reg.describe("Nope")


def test_describe_all_returns_sorted_metadata():
    reg = WidgetRegistry()
    reg.register("Beta", _W, description="b")
    reg.register("Alpha", _W, description="a")
    names = [m.name for m in reg.describe_all()]
    assert names == ["Alpha", "Beta"]
