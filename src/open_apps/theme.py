"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

Shared design-token theming for OpenApps.

A *theme* is a set of design tokens (colors, typography, shape, spacing)
defined once in ``config/apps/theme/<name>.yaml`` and shared across every
app. Selecting a theme emits a ``:root { --token: value }`` block that all
apps consume via ``var(--token)``. This decouples *look* (theme) from
*structure* (each app's ``layout``).

Selection is done with Hydra overrides:

* ``apps/theme=solarized``       -> global default for every app (the
  ``apps/theme`` group is in ``config/config.yaml``'s defaults list)
* ``apps.todo.theme=solarized``  -> override a single app (falls back to
  the global theme when the app's ``theme`` field is null/unset)

A theme file looks like::

    # @package apps.theme
    name: solarized
    import_url: ""          # optional external stylesheet escape hatch
    tokens:
      color-bg: "#fdf6e3"
      color-fg: "#657b83"
      color-primary: "#268bd2"
      font-family: "'Inter', sans-serif"
      radius: "8px"
      ...

The ``tokens`` mapping is open-ended: every ``key: value`` becomes the CSS
custom property ``--key: value``, so apps can introduce new tokens without
touching this module.

Apps that have not been migrated to design tokens yet (everything except
todo) still build their stylesheet from ``config/apps/<app>/appearance/``.
:func:`legacy_theme_css` bridges the two worlds — see the comment on
``_LEGACY_ALIASES`` below.
"""
from __future__ import annotations

from pathlib import Path

import yaml
from fasthtml.common import Style

# Repo-root/config/apps/theme -- this file lives at src/open_apps/theme.py.
_THEME_DIR = Path(__file__).resolve().parents[2] / "config" / "apps" / "theme"

_DEFAULT_THEME = "default"


def _as_plain(value):
    """Coerce an OmegaConf node (or anything mapping-like) to a plain dict."""
    if value is None:
        return {}
    # OmegaConf DictConfig exposes ``items``; so does a plain dict.
    if hasattr(value, "items"):
        return {k: v for k, v in value.items()}
    return dict(value)


def load_theme(name: str) -> dict:
    """Load a theme's tokens from ``config/apps/theme/<name>.yaml``.

    Returns a dict with at least ``name``, ``tokens`` and ``import_url``.
    Falls back to the default theme when ``name`` is unknown so a bad
    override degrades gracefully instead of raising.
    """
    path = _THEME_DIR / f"{name}.yaml"
    if not path.exists():
        path = _THEME_DIR / f"{_DEFAULT_THEME}.yaml"
    data = yaml.safe_load(path.read_text()) or {}
    data.setdefault("name", name)
    data.setdefault("tokens", {})
    data.setdefault("import_url", "")
    return data


def resolve_theme(apps_config, app_name: str) -> dict:
    """Resolve the effective theme for ``app_name``.

    ``apps_config`` is the ``config.apps`` node handed to every app as
    ``app.config``. Precedence: per-app ``apps.<app_name>.theme`` (a theme
    name string) overrides the global ``apps.theme`` group; a null/unset
    per-app value inherits the global theme.
    """
    app_cfg = getattr(apps_config, app_name, None)
    per_app = getattr(app_cfg, "theme", None) if app_cfg is not None else None
    if per_app:
        return load_theme(str(per_app))

    global_theme = getattr(apps_config, "theme", None)
    if global_theme is not None:
        # Allow global theme to be provided either as a composed config node
        # (apps/theme=<name>) or as a plain string override (apps.theme=<name>). 
        if isinstance(global_theme, str):
            return load_theme(global_theme)
        theme = _as_plain(global_theme)
        theme.setdefault("tokens", {})
        theme["tokens"] = _as_plain(theme["tokens"])
        return theme

    return load_theme(_DEFAULT_THEME)


def render_theme_css(theme: dict) -> str:
    """Build the ``:root`` CSS-variable block (plus optional import) for a theme.

    ``theme`` is the dict returned by :func:`resolve_theme` / :func:`load_theme`.
    """
    tokens = _as_plain(theme.get("tokens", {}))

    safe_lines: list[str] = []
    for key, value in tokens.items():
        key = str(key)
        # Allow only simple custom-property names to avoid broken CSS/injection.
        if (not key) or any(not (c.isalnum() or c in "-_") for c in key):
            continue
        val = str(value).replace("\n", " ").replace("\r", " ")
        # Prevent breaking out of <style> tags if a theme value contains HTML.
        if "<" in val or ">" in val:
            continue
        safe_lines.append(f"  --{key}: {val};")

    lines = "\n".join(safe_lines)

    import_url = (theme.get("import_url") or "").strip()
    # Avoid breaking out of the quoted @import string.
    if any(c in import_url for c in ('"', "'", "\n", "\r")):
        import_url = ""

    import_rule = f'@import url("{import_url}");\n' if import_url else ""
    return f"{import_rule}:root {{\n{lines}\n}}"


def render_theme_tokens(theme: dict) -> Style:
    """:func:`render_theme_css` wrapped in a FastHTML ``Style`` element."""
    return Style(render_theme_css(theme))


def theme_style(apps_config, app_name: str) -> Style:
    """Convenience: resolve + render the token block for ``app_name`` in one call.

    Call this per-request so live ``reconfigure`` theme swaps take effect.
    """
    return render_theme_tokens(resolve_theme(apps_config, app_name))


# --------------------------------------------------------------------------
# Bridge for apps that still render `config/apps/<app>/appearance/*.yaml`
# --------------------------------------------------------------------------
#
# Only the todo app has been rewritten against design tokens. The other apps
# build a `:root` block of their own from their `appearance` config and read
# those names throughout their stylesheets, so a theme selection was invisible
# to them. Rewriting five stylesheets is a much larger change than this one;
# until then, re-point the legacy custom properties at the shared tokens.
#
# Keys are the legacy custom-property names those apps declare; values are the
# token they should follow. `--font-family` needs no entry: the theme declares
# that name itself, and the bridge block is emitted after the app's own
# stylesheet, so the token value already wins.
_LEGACY_ALIASES: dict[str, str] = {
    # calendar
    "primary": "color-primary",
    "primary-hover": "color-accent",
    "secondary": "color-neutral",
    "background": "color-bg",
    "text": "color-fg",
    "error": "color-danger",
    "border": "color-border",
    "heading-font": "font-heading",
    "base-font-size": "font-size-base",
    "button-border-radius": "radius",
    # messenger + code editor
    "custom-font-family": "font-family",
    "custom-font-size": "font-size-base",
    "custom-font-color": "color-fg",
    "custom-background-color": "color-bg",
    "chat-font-family": "font-family",
    "chat-font-size": "font-size-base",
    "chat-font-color": "color-fg",
    "chat-header-font-color": "color-muted",
    "chat-primary-bubble-color": "color-primary",
    "chat-secondary-bubble-color": "color-surface",
    "chat-display-background-color": "color-bg",
    "main-bg-color": "color-surface",
}

# The page chrome every app shares. Aliasing alone is not enough: an app only
# picks up a token where its own CSS happens to use a variable, and several
# paint the page from hard-coded values.
_LEGACY_BASE_CSS = """
html, body {
  background-color: var(--color-bg);
  color: var(--color-fg);
  font-family: var(--font-family);
}
"""

# Per-app selectors whose colors are hard-coded in the app's stylesheet (so no
# alias can reach them) but which cover enough of the page that leaving them
# unthemed reads as "the theme did nothing".
_LEGACY_APP_CSS: dict[str, str] = {
    "maps": """
#sidebar { background: var(--color-surface); }
#sidebar h2, #sidebar h3 { color: var(--color-fg); }
.popup-list-item, .route-result, .saved-place { background: var(--color-surface); }
""",
    "code_editor": """
.main-content { background-color: var(--color-surface); }
textarea, textarea.styled-content { background-color: var(--color-surface); }
""",
    "messenger": """
.bg-base-100, .bg-base-200 { background-color: var(--color-surface); }
""",
    "start_page": """
#wrapper > .wrapper { background-color: var(--color-bg); }
""",
}


def legacy_theme_css(apps_config, app_name: str) -> str:
    """CSS that makes the active theme visible in an app built on ``appearance``.

    Emits the theme's token block, the legacy-variable aliases, and the shared
    page chrome. Returns ``""`` for the ``default`` theme: unmigrated apps are
    still described entirely by their ``appearance`` config, so a deployment
    that never selects a theme must render exactly as it did before.

    Emit the result *after* the app's own stylesheet — the bridge relies on
    document order, not specificity, to win.
    """
    theme = resolve_theme(apps_config, app_name)
    if str(theme.get("name", _DEFAULT_THEME)) == _DEFAULT_THEME:
        return ""

    tokens = _as_plain(theme.get("tokens", {}))
    aliases = "\n".join(
        f"  --{legacy}: var(--{token});"
        for legacy, token in _LEGACY_ALIASES.items()
        if token in tokens
    )
    return "\n".join(
        [
            render_theme_css(theme),
            f":root {{\n{aliases}\n}}",
            _LEGACY_BASE_CSS,
            _LEGACY_APP_CSS.get(app_name, ""),
        ]
    )


def legacy_theme_style(apps_config, app_name: str) -> Style:
    """:func:`legacy_theme_css` as a FastHTML ``Style`` (empty on the default theme)."""
    return Style(legacy_theme_css(apps_config, app_name))
