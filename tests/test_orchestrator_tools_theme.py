import json
from pathlib import Path

import pytest
from textual.app import App

from patchbai.agents.fake_sdk_adapter import FakeSDKAdapter
from patchbai.agents.manager import AgentManager
from patchbai.events import EventBus
from patchbai.orchestrator.tools import build_orchestrator_tools
from patchbai.persistence.themes_store import NamedThemesStore
from patchbai.theme.spec import ThemePalette, ThemeSpec


def _make_manager(tmp_path, ok_script):
    return AgentManager(
        cwd=tmp_path,
        bus=EventBus(),
        adapter_factory=lambda: FakeSDKAdapter(scripts=[ok_script()]),
    )


def _spec_dict(primary: str = "#112233") -> dict:
    return ThemeSpec(palette=ThemePalette(primary=primary)).model_dump(mode="json")


class _StubApp(App):
    """Bare App used as the `app` arg for tool wiring. Avoids spinning up
    the full PatchbaiApp."""


@pytest.mark.asyncio
async def test_set_theme_invokes_apply_theme(tmp_path, ok_script):
    manager = _make_manager(tmp_path, ok_script)
    store = NamedThemesStore(global_dir=tmp_path)
    host = _StubApp()
    async with host.run_test():
        host._active_theme_extra_css = ""
        tools = build_orchestrator_tools(
            manager, themes_store=store, app=host,
        )
        out = await tools["set_theme"]({"spec": _spec_dict("#aabbcc")})
        assert "applied" in out["content"][0]["text"].lower()
        assert host.theme.startswith("patchbai:")


@pytest.mark.asyncio
async def test_set_theme_with_invalid_spec_returns_error_text(tmp_path, ok_script):
    manager = _make_manager(tmp_path, ok_script)
    store = NamedThemesStore(global_dir=tmp_path)
    host = _StubApp()
    async with host.run_test():
        host._active_theme_extra_css = ""
        tools = build_orchestrator_tools(
            manager, themes_store=store, app=host,
        )
        out = await tools["set_theme"]({"spec": {"bogus": True}})
        assert "invalid" in out["content"][0]["text"].lower()


@pytest.mark.asyncio
async def test_save_theme_with_explicit_spec_persists(tmp_path, ok_script):
    manager = _make_manager(tmp_path, ok_script)
    store = NamedThemesStore(global_dir=tmp_path)
    host = _StubApp()
    async with host.run_test():
        host._active_theme_extra_css = ""
        tools = build_orchestrator_tools(
            manager, themes_store=store, app=host,
        )
        out = await tools["save_theme"](
            {"name": "alpha", "spec": _spec_dict("#445566")}
        )
        assert "saved" in out["content"][0]["text"].lower()
        assert store.load("alpha").palette.primary == "#445566"


@pytest.mark.asyncio
async def test_save_theme_without_spec_snapshots_active(tmp_path, ok_script):
    """save_theme with no spec must read the live palette + cached extra_css."""
    manager = _make_manager(tmp_path, ok_script)
    store = NamedThemesStore(global_dir=tmp_path)
    host = _StubApp()
    async with host.run_test() as pilot:
        await pilot.pause()
        host._active_theme_extra_css = "X { color: red; }"
        tools = build_orchestrator_tools(
            manager, themes_store=store, app=host,
        )
        out = await tools["save_theme"]({"name": "snapshot"})
        assert "saved" in out["content"][0]["text"].lower()
        loaded = store.load("snapshot")
        assert loaded is not None
        assert loaded.extra_css == "X { color: red; }"
        # Palette mirrors host.current_theme (whatever Textual default is).
        assert loaded.palette.primary  # non-empty


@pytest.mark.asyncio
async def test_load_theme_applies_saved(tmp_path, ok_script):
    manager = _make_manager(tmp_path, ok_script)
    store = NamedThemesStore(global_dir=tmp_path)
    store.save(
        "alpha",
        ThemeSpec(palette=ThemePalette(primary="#998877")),
    )
    host = _StubApp()
    async with host.run_test():
        host._active_theme_extra_css = ""
        tools = build_orchestrator_tools(
            manager, themes_store=store, app=host,
        )
        out = await tools["load_theme"]({"name": "alpha", "persist": False})
        assert "loaded" in out["content"][0]["text"].lower()
        assert host.theme == "patchbai:alpha"


@pytest.mark.asyncio
async def test_load_theme_falls_through_to_builtin(tmp_path, ok_script):
    manager = _make_manager(tmp_path, ok_script)
    store = NamedThemesStore(global_dir=tmp_path)
    host = _StubApp()
    async with host.run_test():
        host._active_theme_extra_css = ""
        tools = build_orchestrator_tools(
            manager, themes_store=store, app=host,
        )
        out = await tools["load_theme"]({"name": "nord", "persist": False})
        text = out["content"][0]["text"].lower()
        assert "loaded" in text
        assert host.theme == "nord"


@pytest.mark.asyncio
async def test_load_theme_unknown_name_returns_error(tmp_path, ok_script):
    manager = _make_manager(tmp_path, ok_script)
    store = NamedThemesStore(global_dir=tmp_path)
    host = _StubApp()
    async with host.run_test():
        host._active_theme_extra_css = ""
        tools = build_orchestrator_tools(
            manager, themes_store=store, app=host,
        )
        out = await tools["load_theme"](
            {"name": "no-such-theme-anywhere", "persist": False}
        )
        text = out["content"][0]["text"].lower()
        assert "not found" in text or "unknown" in text


@pytest.mark.asyncio
async def test_list_themes_returns_saved_and_builtin(tmp_path, ok_script):
    manager = _make_manager(tmp_path, ok_script)
    store = NamedThemesStore(global_dir=tmp_path)
    store.save("alpha", ThemeSpec(palette=ThemePalette(primary="#aabbcc")))
    host = _StubApp()
    async with host.run_test():
        host._active_theme_extra_css = ""
        tools = build_orchestrator_tools(
            manager, themes_store=store, app=host,
        )
        out = await tools["list_themes"]({})
        payload = json.loads(out["content"][0]["text"])
        assert "alpha" in payload["saved"]
        assert "nord" in payload["builtin"]  # built-in always present
        assert "active" in payload


@pytest.mark.asyncio
async def test_get_theme_with_name_returns_saved(tmp_path, ok_script):
    manager = _make_manager(tmp_path, ok_script)
    store = NamedThemesStore(global_dir=tmp_path)
    store.save("alpha", ThemeSpec(palette=ThemePalette(primary="#aabbcc")))
    host = _StubApp()
    async with host.run_test():
        host._active_theme_extra_css = ""
        tools = build_orchestrator_tools(
            manager, themes_store=store, app=host,
        )
        out = await tools["get_theme"]({"name": "alpha"})
        payload = json.loads(out["content"][0]["text"])
        assert payload["palette"]["primary"] == "#aabbcc"


@pytest.mark.asyncio
async def test_get_theme_no_name_returns_active(tmp_path, ok_script):
    manager = _make_manager(tmp_path, ok_script)
    store = NamedThemesStore(global_dir=tmp_path)
    host = _StubApp()
    async with host.run_test() as pilot:
        await pilot.pause()
        host._active_theme_extra_css = "X { color: red; }"
        tools = build_orchestrator_tools(
            manager, themes_store=store, app=host,
        )
        out = await tools["get_theme"]({})
        payload = json.loads(out["content"][0]["text"])
        assert "name" in payload
        assert "palette" in payload
        assert payload["extra_css"] == "X { color: red; }"


@pytest.mark.asyncio
async def test_load_theme_persist_global_writes_config(tmp_path, ok_script):
    """persist=True, scope=global writes the active theme to config_store."""
    from patchbai.config import ConfigStore

    manager = _make_manager(tmp_path, ok_script)
    store = NamedThemesStore(global_dir=tmp_path)
    config_store = ConfigStore(global_dir=tmp_path)
    store.save("alpha", ThemeSpec(palette=ThemePalette(primary="#aabbcc")))

    host = _StubApp()
    async with host.run_test():
        host._active_theme_extra_css = ""
        tools = build_orchestrator_tools(
            manager, themes_store=store, config_store=config_store, app=host,
        )
        out = await tools["load_theme"]({"name": "alpha"})  # default persist=True, scope=global
        assert "loaded" in out["content"][0]["text"].lower()
        assert "warning" not in out["content"][0]["text"].lower()
        assert config_store.load().ui.active_theme == "alpha"


@pytest.mark.asyncio
async def test_load_theme_persist_global_warns_when_no_config_store(tmp_path, ok_script):
    """persist=True, scope=global without config_store returns a warning."""
    manager = _make_manager(tmp_path, ok_script)
    store = NamedThemesStore(global_dir=tmp_path)
    store.save("alpha", ThemeSpec(palette=ThemePalette(primary="#aabbcc")))

    host = _StubApp()
    async with host.run_test():
        host._active_theme_extra_css = ""
        tools = build_orchestrator_tools(
            manager, themes_store=store, app=host,  # no config_store
        )
        out = await tools["load_theme"]({"name": "alpha"})
        text = out["content"][0]["text"].lower()
        assert "loaded" in text
        assert "warning" in text


@pytest.mark.asyncio
async def test_load_theme_invalid_scope_returns_error(tmp_path, ok_script):
    manager = _make_manager(tmp_path, ok_script)
    store = NamedThemesStore(global_dir=tmp_path)
    store.save("alpha", ThemeSpec(palette=ThemePalette(primary="#aabbcc")))

    host = _StubApp()
    async with host.run_test():
        host._active_theme_extra_css = ""
        tools = build_orchestrator_tools(
            manager, themes_store=store, app=host,
        )
        out = await tools["load_theme"](
            {"name": "alpha", "persist": True, "scope": "workspace"}
        )
        text = out["content"][0]["text"].lower()
        assert "invalid scope" in text or "scope" in text
