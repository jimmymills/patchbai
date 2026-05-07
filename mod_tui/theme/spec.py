from pydantic import BaseModel, ConfigDict, Field


class ThemePalette(BaseModel):
    """Maps 1:1 to textual.theme.Theme constructor args."""
    model_config = ConfigDict(extra="forbid")

    primary: str
    secondary: str | None = None
    warning: str | None = None
    error: str | None = None
    success: str | None = None
    accent: str | None = None
    foreground: str | None = None
    background: str | None = None
    surface: str | None = None
    panel: str | None = None
    boost: str | None = None
    dark: bool = True
    luminosity_spread: float = 0.15
    text_alpha: float = 0.95
    variables: dict[str, str] = Field(default_factory=dict)


class ThemeSpec(BaseModel):
    """Saved theme. Applied to a live App via theme.engine.apply_theme."""
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    palette: ThemePalette
    extra_css: str = ""
