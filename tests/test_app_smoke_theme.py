import json
from pathlib import Path

import pytest

from mod_tui.app import ModTuiApp
from mod_tui.config import ConfigStore
from mod_tui.persistence.themes_store import NamedThemesStore
from mod_tui.theme.spec import ThemePalette, ThemeSpec


@pytest.mark.asyncio
async def test_boot_seeds_default_theme(tmp_path: Path):
    """First-run boot writes a 'default' theme to ~/.config/mod_tui/themes/."""
    global_dir = tmp_path / "config"
    cwd = tmp_path / "project"
    cwd.mkdir()

    app = ModTuiApp(cwd=cwd, global_dir=global_dir)
    async with app.run_test() as pilot:
        await pilot.pause()

    store = NamedThemesStore(global_dir=global_dir)
    assert "default" in store.list()
    spec = store.load("default")
    assert spec is not None
    assert spec.palette.primary  # has SOMETHING


@pytest.mark.asyncio
async def test_boot_does_not_overwrite_existing_default(tmp_path: Path):
    """If user has saved their own 'default', boot must not clobber it."""
    global_dir = tmp_path / "config"
    cwd = tmp_path / "project"
    cwd.mkdir()
    seed_store = NamedThemesStore(global_dir=global_dir)
    seed_store.save(
        "default",
        ThemeSpec(palette=ThemePalette(primary="#deadbe")),
    )

    app = ModTuiApp(cwd=cwd, global_dir=global_dir)
    async with app.run_test() as pilot:
        await pilot.pause()

    after = seed_store.load("default")
    assert after.palette.primary == "#deadbe"


@pytest.mark.asyncio
async def test_boot_with_workspace_active_theme_applies_builtin(tmp_path: Path):
    global_dir = tmp_path / "config"
    cwd = tmp_path / "project"
    cwd.mkdir()
    # Pre-seed workspace.json with active_theme="nord".
    project_state = cwd / ".mod_tui"
    project_state.mkdir()
    (project_state / "workspace.json").write_text(json.dumps({
        "version": 1,
        "tabs": [
            {"id": "default", "title": "default", "layout": {
                "version": 1,
                "layout": {"id": "orch", "widget": "OrchestratorChat"},
            }},
        ],
        "active": "default",
        "active_theme": "nord",
    }))

    app = ModTuiApp(cwd=cwd, global_dir=global_dir)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.theme == "nord"


@pytest.mark.asyncio
async def test_boot_with_global_active_theme_applies_builtin(tmp_path: Path):
    global_dir = tmp_path / "config"
    cwd = tmp_path / "project"
    cwd.mkdir()
    # Pre-seed config.toml with ui.active_theme="gruvbox".
    cfg_store = ConfigStore(global_dir=global_dir)
    cfg = cfg_store.load()
    cfg.ui.active_theme = "gruvbox"
    cfg_store.save(cfg)

    app = ModTuiApp(cwd=cwd, global_dir=global_dir)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.theme == "gruvbox"


@pytest.mark.asyncio
async def test_boot_with_corrupted_active_theme_falls_back(tmp_path: Path):
    """Active theme that doesn't exist anywhere should not crash boot."""
    global_dir = tmp_path / "config"
    cwd = tmp_path / "project"
    cwd.mkdir()
    cfg_store = ConfigStore(global_dir=global_dir)
    cfg = cfg_store.load()
    cfg.ui.active_theme = "no-such-theme-xyz"
    cfg_store.save(cfg)

    app = ModTuiApp(cwd=cwd, global_dir=global_dir)
    async with app.run_test() as pilot:
        await pilot.pause()
        # App is alive; theme was either left at Textual default or
        # fell back to default. We just need it not to crash.
        assert app.theme is not None
