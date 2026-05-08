import pytest

from patchfeld.agents.fake_sdk_adapter import FakeSDKAdapter
from patchfeld.agents.manager import AgentManager
from patchfeld.events import EventBus
from patchfeld.layout.registry import WidgetRegistry
from patchfeld.layout.spec import LayoutSpec
from patchfeld.orchestrator.tools import build_orchestrator_tools
from patchfeld.persistence.layouts_store import NamedLayoutsStore


def _make(tmp_path, ok_script):
    return AgentManager(
        cwd=tmp_path, bus=EventBus(),
        adapter_factory=lambda: FakeSDKAdapter(scripts=[ok_script()]),
    )


@pytest.mark.asyncio
async def test_set_layout_registers_custom_widget_before_apply(tmp_path, ok_script):
    manager = _make(tmp_path, ok_script)
    store = NamedLayoutsStore(global_dir=tmp_path)
    registry = WidgetRegistry()
    from textual.widgets import Static
    registry.register("OrchestratorChat", Static)

    applied: list[LayoutSpec] = []
    async def apply_callable(spec, *, layout_name=None, tab_id=None):
        applied.append(spec)

    tools = build_orchestrator_tools(
        manager,
        apply_layout=apply_callable,
        layouts_store=store,
        widget_registry=registry,
    )
    set_layout = tools["set_layout"]

    spec_dict = {
        "version": 1,
        "layout": {
            "type": "horizontal",
            "children": [
                {"id": "orch", "widget": "OrchestratorChat"},
                {"id": "fancy", "widget": "Fancy"},
            ],
        },
        "custom_widgets": [
            {"name": "Fancy", "source":
                "from textual.widgets import Static\n"
                "class Fancy(Static):\n"
                "    pass\n"},
        ],
    }
    out = await set_layout({"spec": spec_dict})
    assert "applied" in out["content"][0]["text"].lower()
    assert applied  # apply was called
    assert registry.get("Fancy").__name__ == "Fancy"


@pytest.mark.asyncio
async def test_set_layout_with_invalid_custom_widget_aborts(tmp_path, ok_script):
    manager = _make(tmp_path, ok_script)
    store = NamedLayoutsStore(global_dir=tmp_path)
    registry = WidgetRegistry()
    from textual.widgets import Static
    registry.register("OrchestratorChat", Static)

    applied: list = []
    async def apply_callable(spec, *, layout_name=None, tab_id=None):
        applied.append(spec)

    tools = build_orchestrator_tools(
        manager,
        apply_layout=apply_callable,
        layouts_store=store,
        widget_registry=registry,
    )
    set_layout = tools["set_layout"]

    spec_dict = {
        "version": 1,
        "layout": {"id": "orch", "widget": "OrchestratorChat"},
        "custom_widgets": [
            {"name": "Broken", "source": "this is not valid python\n"},
        ],
    }
    out = await set_layout({"spec": spec_dict})
    text = out["content"][0]["text"].lower()
    assert "error" in text or "broken" in text
    assert applied == []
