import pytest
from textual.widget import Widget

from patchfeld.layout.custom_widgets import (
    CustomWidgetError,
    register_custom_widget,
)
from patchfeld.layout.registry import WidgetRegistry


def test_register_with_widget_class_sentinel():
    reg = WidgetRegistry()
    src = """
from textual.widgets import Static

class MyPanel(Static):
    pass

WIDGET_CLASS = MyPanel
"""
    register_custom_widget(reg, "Banner", src)
    cls = reg.get("Banner")
    assert issubclass(cls, Widget)
    assert cls.__name__ == "MyPanel"


def test_register_class_named_after_widget_name():
    reg = WidgetRegistry()
    src = """
from textual.widgets import Static

class Banner(Static):
    pass
"""
    register_custom_widget(reg, "Banner", src)
    cls = reg.get("Banner")
    assert cls.__name__ == "Banner"


def test_register_single_widget_subclass_inferred():
    reg = WidgetRegistry()
    src = """
from textual.widgets import Static

class TheOnlyOne(Static):
    pass
"""
    register_custom_widget(reg, "Anything", src)
    cls = reg.get("Anything")
    assert cls.__name__ == "TheOnlyOne"


def test_register_raises_when_no_widget_subclass():
    reg = WidgetRegistry()
    src = "x = 42\n"
    with pytest.raises(CustomWidgetError, match="no Widget subclass"):
        register_custom_widget(reg, "Nope", src)


def test_register_raises_on_exec_error():
    reg = WidgetRegistry()
    src = "this is not valid python\n"
    with pytest.raises(CustomWidgetError, match="exec"):
        register_custom_widget(reg, "Nope", src)


def test_register_raises_when_multiple_widget_subclasses_and_no_sentinel():
    reg = WidgetRegistry()
    src = """
from textual.widgets import Static

class A(Static):
    pass

class B(Static):
    pass
"""
    with pytest.raises(CustomWidgetError, match="ambiguous"):
        register_custom_widget(reg, "X", src)


def test_register_re_register_replaces_class():
    reg = WidgetRegistry()
    src_v1 = """
from textual.widgets import Static
class MyPanel(Static):
    pass
WIDGET_CLASS = MyPanel
"""
    src_v2 = """
from textual.widgets import Static
class MyPanelV2(Static):
    pass
WIDGET_CLASS = MyPanelV2
"""
    register_custom_widget(reg, "Panel", src_v1)
    register_custom_widget(reg, "Panel", src_v2)
    cls = reg.get("Panel")
    assert cls.__name__ == "MyPanelV2"


def test_register_custom_widget_marks_source_inline():
    reg = WidgetRegistry()
    src = """
from textual.widgets import Static
class Banner(Static):
    pass
"""
    register_custom_widget(reg, "Banner", src)
    assert reg.describe("Banner").source == "inline"
