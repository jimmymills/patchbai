"""Apply a ThemeSpec to a live Textual App.

apply_theme() is the single seam every theme-load path goes through:
    - the load_theme orchestrator tool,
    - the theme switcher modal,
    - boot-time apply.

The function is idempotent under same-name re-apply.
"""
from textual.app import App
from textual.css.tokenize import tokenize
from textual.theme import Theme

from patchbai.theme.spec import ThemePalette, ThemeSpec

_EXTRA_CSS_KEY = ("patchbai_theme", "extra_css")
_THEME_NAME_PREFIX = "patchbai:"


def palette_from_textual_theme(textual_theme) -> ThemePalette:
    """Snapshot a live textual.theme.Theme into our ThemePalette."""
    return ThemePalette(
        primary=textual_theme.primary,
        secondary=textual_theme.secondary,
        warning=textual_theme.warning,
        error=textual_theme.error,
        success=textual_theme.success,
        accent=textual_theme.accent,
        foreground=textual_theme.foreground,
        background=textual_theme.background,
        surface=textual_theme.surface,
        panel=textual_theme.panel,
        boost=textual_theme.boost,
        dark=textual_theme.dark,
        luminosity_spread=textual_theme.luminosity_spread,
        text_alpha=textual_theme.text_alpha,
        variables=dict(textual_theme.variables),
    )


async def apply_theme(app: App, spec: ThemeSpec, *, theme_name: str) -> None:
    """Register/update the theme, set it active, and (re)install extra_css.

    Order of operations: validate everything that can fail BEFORE mutating
    app.theme. If anything raises, the previous theme stays active.
    """
    # 1. Pre-validate extra_css by tokenizing it on the raw tokenizer.
    #    This catches structural/syntax errors (e.g. unclosed braces) without
    #    trying to resolve $variables — which are theme-time, not parse-time.
    if spec.extra_css:
        list(tokenize(spec.extra_css, _EXTRA_CSS_KEY))  # raises TokenError on bad syntax

    # 2. Build the Textual Theme. Will raise on bad color strings.
    # Note: palette validation is best-effort — Textual accepts color strings opaquely.
    full_name = f"{_THEME_NAME_PREFIX}{theme_name}"
    theme = Theme(name=full_name, **spec.palette.model_dump())

    # 3. Replace any prior registration for this name. register_theme would
    #    raise on duplicate, and we want re-apply to mean "swap in place."
    if full_name in app.available_themes:
        app.unregister_theme(full_name)

    # 4. Register and activate. Reactive watcher refreshes $primary etc.
    app.register_theme(theme)
    app.theme = full_name

    # 5. Swap the named CSS source.
    if _EXTRA_CSS_KEY in app.stylesheet.source:
        del app.stylesheet.source[_EXTRA_CSS_KEY]
    if spec.extra_css:
        app.stylesheet.add_source(spec.extra_css, read_from=_EXTRA_CSS_KEY)
    app.refresh_css()

    # 6. Cache the applied extra_css for snapshotting (save_theme).
    app._active_theme_extra_css = spec.extra_css
