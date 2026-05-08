import pytest

from patchfeld.actions import ActionRegistry
from patchfeld.agents.fake_sdk_adapter import FakeSDKAdapter
from patchfeld.agents.manager import AgentManager
from patchfeld.config import ConfigStore
from patchfeld.events import EventBus
from patchfeld.orchestrator.tools import build_orchestrator_tools
from patchfeld.persistence.layouts_store import NamedLayoutsStore


def _make(tmp_path, ok_script):
    return AgentManager(
        cwd=tmp_path, bus=EventBus(),
        adapter_factory=lambda: FakeSDKAdapter(scripts=[ok_script()]),
    )


def test_build_orchestrator_tools_returns_dict_keyed_by_name(tmp_path, ok_script):
    manager = _make(tmp_path, ok_script)
    tools = build_orchestrator_tools(manager)
    assert isinstance(tools, dict)
    assert set(tools.keys()) == {
        "spawn_agent", "list_agents", "read_agent_transcript",
        "send_to_agent", "interrupt_agent", "kill_agent",
        "respond_to_agent_request",
    }


def test_build_orchestrator_tools_with_layout_kwargs(tmp_path, ok_script):
    manager = _make(tmp_path, ok_script)
    store = NamedLayoutsStore(global_dir=tmp_path)

    async def _apply(spec, *, layout_name=None):
        pass

    tools = build_orchestrator_tools(manager, apply_layout=_apply, layouts_store=store)
    assert "set_layout" in tools
    assert "save_layout" in tools
    assert "load_layout" in tools
    assert "list_layouts" in tools


def test_build_orchestrator_tools_with_config_kwargs(tmp_path, ok_script):
    manager = _make(tmp_path, ok_script)
    store = ConfigStore(global_dir=tmp_path)
    actions = ActionRegistry()

    tools = build_orchestrator_tools(manager, config_store=store, actions=actions)
    assert "bind_key" in tools
    assert "unbind_key" in tools
    assert "set_config" in tools
    assert "get_config" in tools
    assert "list_actions" in tools
    assert "list_bindings" in tools
