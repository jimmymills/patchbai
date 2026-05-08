import pytest
from textual.widget import Widget

from patchfeld.layout.registry import WidgetRegistry, UnknownWidgetError


class _W(Widget):
    pass


def test_register_then_get():
    reg = WidgetRegistry()
    reg.register("MyWidget", _W)
    assert reg.get("MyWidget") is _W


def test_get_unknown_raises():
    reg = WidgetRegistry()
    with pytest.raises(UnknownWidgetError, match="Nope") as exc_info:
        reg.get("Nope")
    assert isinstance(exc_info.value, KeyError)


def test_double_register_replaces():
    reg = WidgetRegistry()

    class _A(Widget): ...
    class _B(Widget): ...

    reg.register("X", _A)
    reg.register("X", _B)
    assert reg.get("X") is _B


def test_known_returns_registered_names():
    reg = WidgetRegistry()
    reg.register("A", _W)
    reg.register("B", _W)
    assert set(reg.known()) == {"A", "B"}
