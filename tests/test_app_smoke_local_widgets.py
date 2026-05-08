from pathlib import Path

import pytest

from patchfeld.agents.fake_sdk_adapter import FakeSDKAdapter
from patchfeld.agents.manager import AgentManager
from patchfeld.app import PatchfeldApp
from patchfeld.events import EventBus


def _ok_script():
    from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock
    return [
        AssistantMessage(content=[TextBlock(text="ok")], model="fake-model"),
        ResultMessage(
            subtype="success", duration_ms=1, duration_api_ms=1,
            is_error=False, num_turns=1, session_id="fake",
            total_cost_usd=0.0, usage={"input_tokens": 1, "output_tokens": 1},
            result="ok",
        ),
    ]


def _write_widget(global_dir: Path, name: str, body: str) -> None:
    wdir = global_dir / "widgets"
    wdir.mkdir(parents=True, exist_ok=True)
    (wdir / f"{name}.py").write_text(body, encoding="utf-8")


@pytest.mark.asyncio
async def test_app_loads_local_widget_into_registry(tmp_path):
    _write_widget(tmp_path, "banner", """
from textual.widgets import Static
class Banner(Static):
    pass
""")
    bus = EventBus()
    manager = AgentManager(
        cwd=tmp_path, bus=bus,
        adapter_factory=lambda: FakeSDKAdapter(scripts=[_ok_script()]),
    )
    app = PatchfeldApp(cwd=tmp_path, manager=manager, global_dir=tmp_path)
    assert "Banner" in app.registry.known()
    assert app.registry.describe("Banner").source == "local"
    assert any(o.status == "ok" for o in app._local_widget_outcomes)


@pytest.mark.asyncio
async def test_app_survives_broken_local_widget(tmp_path):
    _write_widget(tmp_path, "broken", "this is not valid python\n")
    bus = EventBus()
    manager = AgentManager(
        cwd=tmp_path, bus=bus,
        adapter_factory=lambda: FakeSDKAdapter(scripts=[_ok_script()]),
    )
    app = PatchfeldApp(cwd=tmp_path, manager=manager, global_dir=tmp_path)
    statuses = [o.status for o in app._local_widget_outcomes]
    assert "import_error" in statuses
    # And the rest of the app constructs cleanly.
    assert "OrchestratorChat" in app.registry.known()


@pytest.mark.asyncio
async def test_app_skips_loading_when_disabled(tmp_path):
    _write_widget(tmp_path, "banner", """
from textual.widgets import Static
class Banner(Static):
    pass
""")
    # Pre-write a config that disables the feature.
    (tmp_path / "config.toml").write_text(
        "[widgets]\nlocal_dir_enabled = false\n", encoding="utf-8",
    )
    bus = EventBus()
    manager = AgentManager(
        cwd=tmp_path, bus=bus,
        adapter_factory=lambda: FakeSDKAdapter(scripts=[_ok_script()]),
    )
    app = PatchfeldApp(cwd=tmp_path, manager=manager, global_dir=tmp_path)
    assert "Banner" not in app.registry.known()
    assert app._local_widget_outcomes == []
