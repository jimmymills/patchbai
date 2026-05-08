import pytest
from textual.widgets import Static

from patchbai.agents.fake_sdk_adapter import FakeSDKAdapter
from patchbai.agents.manager import AgentManager
from patchbai.events import EventBus
from patchbai.layout.registry import WidgetRegistry
from patchbai.orchestrator.tools import build_orchestrator_tools
from patchbai.persistence.layouts_store import NamedLayoutsStore


class _FakeApp:
    """Minimal stand-in for PatchbaiApp — save_widget reads `_global_dir`
    and `registry` from the app reference; list_widgets reads
    `_local_widget_outcomes` for its `errors` array."""
    def __init__(self, global_dir, registry):
        self._global_dir = global_dir
        self.registry = registry
        self._local_widget_outcomes = []


def _tools(tmp_path, registry):
    manager = AgentManager(
        cwd=tmp_path, bus=EventBus(),
        adapter_factory=lambda: FakeSDKAdapter(scripts=[[]]),
    )
    store = NamedLayoutsStore(global_dir=tmp_path)
    app = _FakeApp(tmp_path, registry)
    return build_orchestrator_tools(
        manager,
        layouts_store=store,
        widget_registry=registry,
        app=app,
    )


@pytest.mark.asyncio
async def test_save_widget_writes_file_and_registers_live(tmp_path):
    reg = WidgetRegistry()
    tools = _tools(tmp_path, reg)
    src = (
        "from textual.widgets import Static\n"
        "class Sparkline(Static):\n"
        "    pass\n"
    )
    out = await tools["save_widget"]({"name": "Sparkline", "source": src})
    assert "saved" in out["content"][0]["text"].lower()
    # File on disk.
    written = tmp_path / "widgets" / "Sparkline.py"
    assert written.exists()
    assert written.read_text(encoding="utf-8") == src
    # Live registration.
    assert reg.get("Sparkline").__name__ == "Sparkline"
    assert reg.describe("Sparkline").source == "local"


@pytest.mark.asyncio
async def test_save_widget_rejects_invalid_source(tmp_path):
    reg = WidgetRegistry()
    tools = _tools(tmp_path, reg)
    out = await tools["save_widget"]({
        "name": "Broken",
        "source": "this is not valid python\n",
    })
    assert "cannot save" in out["content"][0]["text"].lower()
    # No file, no registration.
    assert not (tmp_path / "widgets" / "Broken.py").exists()
    assert "Broken" not in reg.known()


@pytest.mark.asyncio
async def test_save_widget_rejects_no_widget_subclass(tmp_path):
    reg = WidgetRegistry()
    tools = _tools(tmp_path, reg)
    out = await tools["save_widget"]({
        "name": "NotAWidget",
        "source": "x = 42\n",
    })
    assert "cannot save" in out["content"][0]["text"].lower()
    assert not (tmp_path / "widgets" / "NotAWidget.py").exists()


@pytest.mark.asyncio
async def test_save_widget_rejects_invalid_name(tmp_path):
    reg = WidgetRegistry()
    tools = _tools(tmp_path, reg)
    for bad in ["", "../escape", "with space", "slash/y"]:
        out = await tools["save_widget"]({"name": bad, "source": "pass\n"})
        assert "invalid" in out["content"][0]["text"].lower()


@pytest.mark.asyncio
async def test_save_widget_rejects_builtin_collision(tmp_path):
    reg = WidgetRegistry()
    reg.register("OrchestratorChat", Static)  # source="builtin" by default
    tools = _tools(tmp_path, reg)
    src = (
        "from textual.widgets import Static\n"
        "class Evil(Static):\n"
        "    pass\n"
    )
    out = await tools["save_widget"]({"name": "OrchestratorChat", "source": src})
    assert "cannot save" in out["content"][0]["text"].lower()
    assert not (tmp_path / "widgets" / "OrchestratorChat.py").exists()
    # Built-in unaffected.
    assert reg.get("OrchestratorChat") is Static


@pytest.mark.asyncio
async def test_save_widget_overwrite_re_registers(tmp_path):
    reg = WidgetRegistry()
    tools = _tools(tmp_path, reg)
    src_v1 = (
        "from textual.widgets import Static\n"
        "class Fancy(Static):\n"
        "    VERSION = 1\n"
    )
    src_v2 = (
        "from textual.widgets import Static\n"
        "class Fancy(Static):\n"
        "    VERSION = 2\n"
    )
    await tools["save_widget"]({"name": "Fancy", "source": src_v1})
    assert reg.get("Fancy").VERSION == 1
    await tools["save_widget"]({"name": "Fancy", "source": src_v2})
    assert reg.get("Fancy").VERSION == 2
    written = tmp_path / "widgets" / "Fancy.py"
    assert "VERSION = 2" in written.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_save_widget_appears_in_list_widgets(tmp_path):
    """End-to-end: after save_widget, the new widget appears in list_widgets
    with source='local' and the metadata block's description/props_schema.

    This test pins the cross-tool contract that the two MCP handlers share
    the same WidgetRegistry instance (so a registration in one is visible
    to the other within the same session, no restart required)."""
    reg = WidgetRegistry()
    # Pre-register a builtin so we can confirm builtins coexist with the new local.
    from textual.widgets import Static
    reg.register("OrchestratorChat", Static)  # source="builtin" by default

    tools = _tools(tmp_path, reg)
    src = (
        "from textual.widgets import Static\n"
        "__patchbai_widget__ = {\n"
        "    'name': 'Sparkline',\n"
        "    'description': 'Token-rate sparkline.',\n"
        "    'props_schema': {'agent_id': str},\n"
        "}\n"
        "class Sparkline(Static):\n"
        "    pass\n"
    )

    save_out = await tools["save_widget"]({"name": "Sparkline", "source": src})
    assert "saved" in save_out["content"][0]["text"].lower()

    # Now call list_widgets and inspect the envelope.
    import json
    list_out = await tools["list_widgets"]({})
    payload = json.loads(list_out["content"][0]["text"])

    assert "widgets" in payload and "errors" in payload
    by_name = {w["name"]: w for w in payload["widgets"]}

    # New local widget is visible with the metadata we provided.
    assert "Sparkline" in by_name
    assert by_name["Sparkline"]["source"] == "local"
    assert by_name["Sparkline"]["description"] == "Token-rate sparkline."
    assert by_name["Sparkline"]["props_schema"] == {"agent_id": "str"}

    # Builtin still present and tagged correctly.
    assert by_name["OrchestratorChat"]["source"] == "builtin"


@pytest.mark.asyncio
async def test_save_widget_failure_does_not_pollute_list_widgets_errors(tmp_path):
    """A failed save_widget returns the error INLINE; list_widgets's `errors`
    array reflects startup-discovery outcomes only, not save_widget failures.

    Pins the asymmetry documented in §10 so a future refactor doesn't quietly
    start tunneling save_widget failures through list_widgets — the orchestrator
    relies on inline error returns from save_widget."""
    reg = WidgetRegistry()
    tools = _tools(tmp_path, reg)

    save_out = await tools["save_widget"]({
        "name": "Broken",
        "source": "this is not valid python\n",
    })
    assert "cannot save" in save_out["content"][0]["text"].lower()

    import json
    list_out = await tools["list_widgets"]({})
    payload = json.loads(list_out["content"][0]["text"])

    # No file was written → no entry in widgets, no entry in errors
    # (because errors comes from the startup loader's outcomes list, which
    # the FakeApp's _local_widget_outcomes — see _tools — leaves empty).
    names = {w["name"] for w in payload["widgets"]}
    assert "Broken" not in names
    error_paths = {e.get("path") for e in payload["errors"]}
    assert not any("Broken" in (p or "") for p in error_paths)
