import json
from pathlib import Path

import pytest

from mod_tui.actions import ActionRegistry
from mod_tui.agents.fake_sdk_adapter import FakeSDKAdapter
from mod_tui.agents.manager import AgentManager
from mod_tui.config import ConfigStore
from mod_tui.events import EventBus
from mod_tui.orchestrator.tools import build_orchestrator_tools


def _make(tmp_path, ok_script):
    manager = AgentManager(
        cwd=tmp_path,
        bus=EventBus(),
        adapter_factory=lambda: FakeSDKAdapter(scripts=[ok_script()]),
    )
    config_store = ConfigStore(global_dir=tmp_path)
    actions = ActionRegistry()
    actions.register(
        "focus_orchestrator", lambda: None,
        description="Focus the orchestrator chat panel.", args_schema={},
    )
    actions.register(
        "focus_panel", lambda panel_id: None,
        description="Focus a specific panel by id.", args_schema={"panel_id": str},
    )
    rebound: list[bool] = []
    def rebind():
        rebound.append(True)
    return manager, config_store, actions, rebind, rebound


@pytest.mark.asyncio
async def test_bind_key_persists_and_triggers_rebind(tmp_path, ok_script):
    manager, store, actions, rebind, rebound = _make(tmp_path, ok_script)
    tools = build_orchestrator_tools(
        manager,
        config_store=store,
        actions=actions,
        rebind_keys=rebind,
    )
    bind_key = tools[7]

    out = await bind_key({"key": "~", "action": "focus_orchestrator"})
    text = out["content"][0]["text"].lower()
    assert "bound" in text
    assert rebound == [True]

    cfg = store.load()
    assert cfg.bindings["~"].action == "focus_orchestrator"


@pytest.mark.asyncio
async def test_bind_key_unknown_action_returns_error(tmp_path, ok_script):
    manager, store, actions, rebind, _ = _make(tmp_path, ok_script)
    tools = build_orchestrator_tools(manager, config_store=store, actions=actions, rebind_keys=rebind)
    bind_key = tools[7]
    out = await bind_key({"key": "~", "action": "no_such_action"})
    assert "unknown action" in out["content"][0]["text"].lower()


@pytest.mark.asyncio
async def test_unbind_key_removes_binding(tmp_path, ok_script):
    manager, store, actions, rebind, _ = _make(tmp_path, ok_script)
    tools = build_orchestrator_tools(manager, config_store=store, actions=actions, rebind_keys=rebind)
    bind_key = tools[7]
    unbind_key = tools[8]

    await bind_key({"key": "~", "action": "focus_orchestrator"})
    out = await unbind_key({"key": "~"})
    assert "unbound" in out["content"][0]["text"].lower()
    cfg = store.load()
    assert "~" not in cfg.bindings


@pytest.mark.asyncio
async def test_set_config_dotted_path(tmp_path, ok_script):
    manager, store, actions, rebind, _ = _make(tmp_path, ok_script)
    tools = build_orchestrator_tools(manager, config_store=store, actions=actions, rebind_keys=rebind)
    set_config = tools[9]
    get_config = tools[10]

    await set_config({"path": "ui.theme", "value": "light"})
    out = await get_config({"path": "ui.theme"})
    assert "light" in out["content"][0]["text"]


@pytest.mark.asyncio
async def test_list_actions_returns_json(tmp_path, ok_script):
    manager, store, actions, rebind, _ = _make(tmp_path, ok_script)
    tools = build_orchestrator_tools(manager, config_store=store, actions=actions, rebind_keys=rebind)
    list_actions = tools[11]

    out = await list_actions({})
    parsed = json.loads(out["content"][0]["text"])
    names = {a["name"] for a in parsed}
    assert "focus_orchestrator" in names
    assert "focus_panel" in names


@pytest.mark.asyncio
async def test_list_bindings_returns_current_bindings(tmp_path, ok_script):
    manager, store, actions, rebind, _ = _make(tmp_path, ok_script)
    tools = build_orchestrator_tools(manager, config_store=store, actions=actions, rebind_keys=rebind)
    bind_key = tools[7]
    list_bindings = tools[12]

    await bind_key({"key": "~", "action": "focus_orchestrator"})
    out = await list_bindings({})
    parsed = json.loads(out["content"][0]["text"])
    assert any(b["key"] == "~" for b in parsed)
