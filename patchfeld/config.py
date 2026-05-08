import logging
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import tomli_w

log = logging.getLogger(__name__)


_DEFAULT_BINDINGS = {
    "/":      ("focus_command_bar", {}),
    "ctrl+q": ("quit", {}),
    "ctrl+h": ("open_history", {}),
    "ctrl+l": ("open_layout_switcher", {}),
    "?":      ("show_help", {}),
}


@dataclass
class KeyBinding:
    action: str
    args: dict = field(default_factory=dict)


@dataclass
class UISection:
    active_theme: str = "default"
    default_model: str = ""


@dataclass
class WidgetsSection:
    local_dir_enabled: bool = True


@dataclass
class Config:
    bindings: dict[str, KeyBinding] = field(default_factory=dict)
    ui: UISection = field(default_factory=UISection)
    widgets: WidgetsSection = field(default_factory=WidgetsSection)

    def get_path(self, path: str) -> Any:
        section, attr = self._split_path(path)
        section_obj = getattr(self, section, None)
        if section_obj is None or not hasattr(section_obj, attr):
            raise KeyError(path)
        return getattr(section_obj, attr)

    def set_path(self, path: str, value: Any) -> None:
        section, attr = self._split_path(path)
        section_obj = getattr(self, section, None)
        if section_obj is None or not hasattr(section_obj, attr):
            raise KeyError(path)
        setattr(section_obj, attr, value)

    @staticmethod
    def _split_path(path: str) -> tuple[str, str]:
        parts = path.split(".")
        if len(parts) != 2:
            raise KeyError(f"only dotted two-segment paths supported, got {path!r}")
        return parts[0], parts[1]


class ConfigStore:
    """Read/write ~/.config/patchfeld/config.toml. Defaults applied on missing file."""

    def __init__(self, global_dir: Path) -> None:
        self._dir = Path(global_dir)
        self._path = self._dir / "config.toml"

    def load(self) -> Config:
        cfg = Config()
        # Apply defaults first.
        for key, (action, args) in _DEFAULT_BINDINGS.items():
            cfg.bindings[key] = KeyBinding(action=action, args=dict(args))

        if not self._path.exists():
            return cfg

        try:
            raw = tomllib.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            log.exception("Failed to parse config.toml; using defaults")
            return cfg

        # Merge bindings (overrides defaults).
        bindings_raw = raw.get("bindings", {})
        if isinstance(bindings_raw, dict):
            for key, val in bindings_raw.items():
                if isinstance(val, dict) and "action" in val:
                    cfg.bindings[key] = KeyBinding(
                        action=val["action"], args=dict(val.get("args", {}))
                    )

        ui_raw = raw.get("ui", {})
        if isinstance(ui_raw, dict):
            if "active_theme" in ui_raw and isinstance(ui_raw["active_theme"], str):
                cfg.ui.active_theme = ui_raw["active_theme"]
            if "default_model" in ui_raw and isinstance(ui_raw["default_model"], str):
                cfg.ui.default_model = ui_raw["default_model"]
            # Legacy `ui.theme` key (now removed) is silently ignored.

        widgets_raw = raw.get("widgets", {})
        if isinstance(widgets_raw, dict):
            if "local_dir_enabled" in widgets_raw and isinstance(
                widgets_raw["local_dir_enabled"], bool
            ):
                cfg.widgets.local_dir_enabled = widgets_raw["local_dir_enabled"]
        return cfg

    def save(self, cfg: Config) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        out = {
            "bindings": {
                key: {"action": b.action, "args": b.args}
                for key, b in cfg.bindings.items()
            },
            "ui": {
                "active_theme": cfg.ui.active_theme,
                "default_model": cfg.ui.default_model,
            },
            "widgets": {
                "local_dir_enabled": cfg.widgets.local_dir_enabled,
            },
        }
        self._path.write_text(tomli_w.dumps(out), encoding="utf-8")
