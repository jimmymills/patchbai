import json
from pathlib import Path

import pytest

from mod_tui.agents.fake_sdk_adapter import FakeSDKAdapter
from mod_tui.agents.manager import AgentManager
from mod_tui.events import EventBus
from mod_tui.layout.defaults import dashboard_layout
from mod_tui.layout.spec import LayoutSpec
from mod_tui.orchestrator.tools import build_orchestrator_tools
from mod_tui.persistence.layouts_store import NamedLayoutsStore


def _make_manager(tmp_path, ok_script):
    return AgentManager(
        cwd=tmp_path,
        bus=EventBus(),
        adapter_factory=lambda: FakeSDKAdapter(scripts=[ok_script()]),
    )


@pytest.mark.asyncio
async def test_set_layout_calls_the_apply_callable(tmp_path, ok_script):
    manager = _make_manager(tmp_path, ok_script)
    store = NamedLayoutsStore(global_dir=tmp_path)
    applied: list[LayoutSpec] = []

    async def apply_callable(spec: LayoutSpec, *, layout_name: str | None = None) -> None:
        applied.append(spec)

    tools = build_orchestrator_tools(
        manager, apply_layout=apply_callable, layouts_store=store
    )
    # Tuple now has 7 + 4 = 11 entries; set_layout is at the end.
    set_layout = tools[7]

    spec_dict = dashboard_layout().model_dump(mode="json")
    out = await set_layout({"spec": spec_dict})
    assert "applied" in out["content"][0]["text"].lower()
    assert applied == [dashboard_layout()]


@pytest.mark.asyncio
async def test_save_layout_then_load_round_trips(tmp_path, ok_script):
    manager = _make_manager(tmp_path, ok_script)
    store = NamedLayoutsStore(global_dir=tmp_path)

    async def apply_callable(spec, *, layout_name=None):
        pass

    tools = build_orchestrator_tools(
        manager, apply_layout=apply_callable, layouts_store=store
    )
    save_layout = tools[8]
    load_layout = tools[9]
    list_layouts = tools[10]

    spec = dashboard_layout()
    out_save = await save_layout({"name": "triage", "spec": spec.model_dump(mode="json")})
    assert "saved" in out_save["content"][0]["text"].lower()
    out_list = await list_layouts({})
    text = out_list["content"][0]["text"]
    assert "triage" in text
    out_load = await load_layout({"name": "triage"})
    assert "loaded" in out_load["content"][0]["text"].lower()


@pytest.mark.asyncio
async def test_set_layout_with_invalid_spec_returns_error_text(tmp_path, ok_script):
    manager = _make_manager(tmp_path, ok_script)
    store = NamedLayoutsStore(global_dir=tmp_path)

    async def apply_callable(spec, *, layout_name=None):
        pass

    tools = build_orchestrator_tools(
        manager, apply_layout=apply_callable, layouts_store=store
    )
    set_layout = tools[7]

    bad = {"version": 1, "layout": {"id": "x", "widget": "AgentTable"}}
    out = await set_layout({"spec": bad})
    assert "error" in out["content"][0]["text"].lower() or "invalid" in out["content"][0]["text"].lower()


@pytest.mark.asyncio
async def test_load_layout_missing_returns_error_text(tmp_path, ok_script):
    manager = _make_manager(tmp_path, ok_script)
    store = NamedLayoutsStore(global_dir=tmp_path)

    async def apply_callable(spec, *, layout_name=None):
        pass

    tools = build_orchestrator_tools(
        manager, apply_layout=apply_callable, layouts_store=store
    )
    load_layout = tools[9]

    out = await load_layout({"name": "nonexistent"})
    text = out["content"][0]["text"].lower()
    assert "not found" in text or "no such layout" in text or "unknown" in text
