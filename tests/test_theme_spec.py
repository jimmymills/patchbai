import pytest
from pydantic import ValidationError

from patchfeld.theme.spec import ThemePalette, ThemeSpec


def test_theme_palette_requires_primary():
    with pytest.raises(ValidationError):
        ThemePalette()  # type: ignore[call-arg]


def test_theme_palette_minimal():
    pal = ThemePalette(primary="#005577")
    assert pal.primary == "#005577"
    assert pal.dark is True
    assert pal.luminosity_spread == 0.15
    assert pal.text_alpha == 0.95
    assert pal.variables == {}
    assert pal.secondary is None


def test_theme_palette_extra_forbidden():
    with pytest.raises(ValidationError):
        ThemePalette(primary="#005577", bogus="x")  # type: ignore[call-arg]


def test_theme_spec_minimal():
    spec = ThemeSpec(palette=ThemePalette(primary="#005577"))
    assert spec.version == 1
    assert spec.extra_css == ""
    assert spec.palette.primary == "#005577"


def test_theme_spec_full_round_trip():
    raw = {
        "version": 1,
        "palette": {
            "primary": "#005577",
            "secondary": "#0099aa",
            "warning": "#ffaa00",
            "error": "#ff0033",
            "success": "#00aa55",
            "accent": "#cc66ff",
            "foreground": "#ffffff",
            "background": "#0a0a0a",
            "surface": "#1a1a1a",
            "panel": "#222222",
            "boost": "#333333",
            "dark": True,
            "luminosity_spread": 0.2,
            "text_alpha": 0.9,
            "variables": {"my-var": "#ff00ff"},
        },
        "extra_css": "OrchestratorChat { border: round $accent; }",
    }
    spec = ThemeSpec.model_validate(raw)
    assert spec.model_dump(mode="json") == raw


def test_theme_spec_extra_forbidden():
    with pytest.raises(ValidationError):
        ThemeSpec.model_validate({
            "palette": {"primary": "#005577"},
            "bogus": True,
        })
