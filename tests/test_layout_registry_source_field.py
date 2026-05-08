from textual.widget import Widget

from patchfeld.layout.registry import WidgetRegistry


class _W(Widget):
    pass


def test_register_default_source_is_builtin():
    reg = WidgetRegistry()
    reg.register("X", _W)
    assert reg.describe("X").source == "builtin"


def test_register_with_explicit_source():
    reg = WidgetRegistry()
    reg.register("X", _W, source="local")
    assert reg.describe("X").source == "local"


def test_describe_all_preserves_source():
    reg = WidgetRegistry()
    reg.register("A", _W, source="builtin")
    reg.register("B", _W, source="local")
    sources = {info.name: info.source for info in reg.describe_all()}
    assert sources == {"A": "builtin", "B": "local"}
