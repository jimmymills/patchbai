import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from textual.widget import Widget

from patchbai.agents.fake_sdk_adapter import FakeSDKAdapter
from patchbai.agents.manager import AgentManager
from patchbai.events import EventBus
from patchbai.layout.local_widgets import LoadOutcome
from patchbai.layout.registry import WidgetRegistry
from patchbai.orchestrator.tools import build_orchestrator_tools


class _W(Widget):
    pass


@pytest.mark.asyncio
async def test_list_widgets_returns_registry_metadata(tmp_path, ok_script):
    reg = WidgetRegistry()
    reg.register("OrchestratorChat", _W, description="manager chat")
    reg.register("Markdown", _W, description="renders markdown",
                 props_schema={"source": str})

    manager = AgentManager(
        cwd=tmp_path, bus=EventBus(),
        adapter_factory=lambda: FakeSDKAdapter(scripts=[ok_script()]),
    )
    tools = build_orchestrator_tools(manager, widget_registry=reg)
    out = await tools["list_widgets"]({})
    payload = json.loads(out["content"][0]["text"])
    assert "widgets" in payload and "errors" in payload
    by_name = {w["name"]: w for w in payload["widgets"]}
    assert "OrchestratorChat" in by_name
    assert by_name["Markdown"]["description"] == "renders markdown"
    assert by_name["Markdown"]["props_schema"] == {"source": "str"}


@pytest.mark.asyncio
async def test_list_widgets_omitted_when_no_registry_passed(tmp_path, ok_script):
    manager = AgentManager(
        cwd=tmp_path, bus=EventBus(),
        adapter_factory=lambda: FakeSDKAdapter(scripts=[ok_script()]),
    )
    tools = build_orchestrator_tools(manager)
    assert "list_widgets" not in tools


@pytest.mark.asyncio
async def test_list_widgets_emits_source_field(tmp_path, ok_script):
    reg = WidgetRegistry()
    # Builtin widget — default source is "builtin".
    reg.register("OrchestratorChat", _W, description="manager chat")
    # Local widget — explicit source="local" (mimics LocalWidgetLoader).
    reg.register("Sparkline", _W, description="local sparkline",
                 source="local")

    manager = AgentManager(
        cwd=tmp_path, bus=EventBus(),
        adapter_factory=lambda: FakeSDKAdapter(scripts=[ok_script()]),
    )
    tools = build_orchestrator_tools(manager, widget_registry=reg, app=None)
    out = await tools["list_widgets"]({})
    payload = json.loads(out["content"][0]["text"])

    assert isinstance(payload["widgets"], list)
    assert len(payload["widgets"]) == 2
    by_name = {w["name"]: w for w in payload["widgets"]}
    assert "source" in by_name["OrchestratorChat"]
    assert "source" in by_name["Sparkline"]
    assert by_name["OrchestratorChat"]["source"] == "builtin"
    assert by_name["Sparkline"]["source"] == "local"


@pytest.mark.asyncio
async def test_list_widgets_emits_errors_array(tmp_path, ok_script):
    reg = WidgetRegistry()
    reg.register("OrchestratorChat", _W, description="manager chat")

    bad_outcome = LoadOutcome(
        path=Path("/fake/path/broken.py"),
        name="broken",
        status="import_error",
        error="ModuleNotFoundError: no module named 'missing'",
    )
    ok_outcome = LoadOutcome(
        path=Path("/fake/path/working.py"),
        name="working",
        status="ok",
        error=None,
    )
    fake_app = SimpleNamespace(_local_widget_outcomes=[bad_outcome, ok_outcome])

    manager = AgentManager(
        cwd=tmp_path, bus=EventBus(),
        adapter_factory=lambda: FakeSDKAdapter(scripts=[ok_script()]),
    )
    tools = build_orchestrator_tools(manager, widget_registry=reg, app=fake_app)
    out = await tools["list_widgets"]({})
    payload = json.loads(out["content"][0]["text"])

    assert "errors" in payload
    assert len(payload["errors"]) == 1
    err = payload["errors"][0]
    assert err["status"] == "import_error"
    assert err["name"] == "broken"
    assert err["path"] == "/fake/path/broken.py"
    assert "ModuleNotFoundError" in err["error"]
    # OK outcomes are NOT included in errors.
    assert all(e["status"] != "ok" for e in payload["errors"])
