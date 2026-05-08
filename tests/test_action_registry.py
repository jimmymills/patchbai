import pytest

from patchfeld.actions import ActionRegistry, ActionSpec


def test_register_then_lookup():
    reg = ActionRegistry()

    def my_action():
        return "ran"

    reg.register("my_action", my_action, description="does the thing", args_schema={})
    spec = reg.get("my_action")
    assert spec.callable is my_action
    assert spec.description == "does the thing"
    assert spec.args_schema == {}


def test_list_returns_specs_sorted_by_name():
    reg = ActionRegistry()
    reg.register("zeta", lambda: None, description="z", args_schema={})
    reg.register("alpha", lambda: None, description="a", args_schema={})
    names = [s.name for s in reg.list()]
    assert names == ["alpha", "zeta"]


def test_get_unknown_raises_keyerror():
    reg = ActionRegistry()
    with pytest.raises(KeyError):
        reg.get("nope")


def test_register_overrides_existing():
    reg = ActionRegistry()
    reg.register("act", lambda: 1, description="v1", args_schema={})
    reg.register("act", lambda: 2, description="v2", args_schema={})
    assert reg.get("act").description == "v2"


def test_invoke_calls_with_args():
    reg = ActionRegistry()
    captured: list = []
    def my_act(panel_id: str):
        captured.append(panel_id)
    reg.register("my_act", my_act, description="x", args_schema={"panel_id": str})

    reg.invoke("my_act", {"panel_id": "orch"})
    assert captured == ["orch"]
