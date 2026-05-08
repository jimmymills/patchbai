import pytest
from textual.widget import Widget

from patchfeld.layout.registry import UnknownWidgetError, WidgetRegistry


class _W(Widget):
    pass


def test_unregister_removes_known_widget():
    reg = WidgetRegistry()
    reg.register("MyWidget", _W)
    reg.unregister("MyWidget")
    with pytest.raises(UnknownWidgetError):
        reg.get("MyWidget")


def test_unregister_unknown_is_noop():
    reg = WidgetRegistry()
    reg.unregister("NeverRegistered")  # must not raise


def test_register_after_unregister_replaces_cleanly():
    reg = WidgetRegistry()
    reg.register("X", _W, description="v1")
    reg.unregister("X")

    class _V2(Widget):
        pass

    reg.register("X", _V2, description="v2")
    assert reg.get("X") is _V2
    assert reg.describe("X").description == "v2"
