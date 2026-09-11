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
*structure* (each app's ``layout``) and from *behaviour* (plain keys in
each app's ``config/apps/<app>/default.yaml``).

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
    assets:
      tone: light
      icon_set: color

The ``tokens`` mapping is open-ended: every ``key: value`` becomes the CSS
custom property ``--key: value``, so apps can introduce new tokens without
touching this module.

``assets`` covers what a CSS variable cannot reach: raster icons, Leaflet
tile layers, CodeMirror stylesheets. It is deliberately *app-agnostic* --
a theme declares intent (``tone: dark``) and each app maps that onto its
own concrete choice, so a shared theme file never has to grow a key for
every app in the repo. See :func:`theme_assets`.
"""
from __future__ import annotations

from pathlib import Path

import yaml
from fasthtml.common import Style

# Repo-root/config/apps/theme -- this file lives at src/open_apps/theme.py.
_THEME_DIR = Path(__file__).resolve().parents[2] / "config" / "apps" / "theme"

_DEFAULT_THEME = "default"

# Fallbacks for a theme that predates (or omits) the `assets` block.
_DEFAULT_ASSETS: dict[str, str] = {"tone": "light", "icon_set": "color"}


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

    Returns a dict with at least ``name``, ``tokens``, ``import_url`` and
    ``assets``. Falls back to the default theme when ``name`` is unknown so
    a bad override degrades gracefully instead of raising.
    """
    if not name or any(not (char.isalnum() or char in "-_") for char in name):
        name = _DEFAULT_THEME
    path = _THEME_DIR / f"{name}.yaml"
    data = yaml.safe_load(path.read_text()) or {}
    data.setdefault("name", name)
    data.setdefault("tokens", {})
    data.setdefault("import_url", "")
    data.setdefault("assets", {})
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
        theme.setdefault("assets", {})
        theme["assets"] = _as_plain(theme["assets"])
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
        # Sanitize values to avoid breaking out of the declaration / <style> context.
        val = (
            str(value)
            .replace("\n", " ")
            .replace("\r", " ")
            .replace(";", " ")
            .replace("}", " ")
            .replace("<", " ")
            .replace(">", " ")
            .strip()
        )
        safe_lines.append(f"  --{key}: {val};")

    lines = "\n".join(safe_lines)

    import_url = (theme.get("import_url") or "").strip()
    # Avoid breaking out of the quoted @import string.
    if any(c in import_url for c in ('"', "'", "\n", "\r", "<", ">")):
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
# Non-CSS theme choices
# --------------------------------------------------------------------------


def theme_assets(apps_config, app_name: str) -> dict:
    """The active theme's ``assets`` block for ``app_name``, with defaults filled.

    Keys are app-agnostic on purpose:

    * ``tone``     -- ``light`` | ``dark`` | ``mono``. Apps map this onto their
      own concrete asset (maps picks a tile layer, the code editor picks a
      CodeMirror stylesheet, the start page picks a tile fill).
    * ``icon_set`` -- ``color`` | ``bw``. Which raster icon directory to serve.

    A theme that omits ``assets`` (or omits a key) gets the light/color
    defaults, so adding a new theme file never has to spell them out.
    """
    assets = dict(_DEFAULT_ASSETS)
    assets.update(_as_plain(resolve_theme(apps_config, app_name).get("assets", {})))
    return assets


def theme_asset(apps_config, app_name: str, key: str, default=None):
    """A single :func:`theme_assets` entry, or ``default`` when unset."""
    return theme_assets(apps_config, app_name).get(key, default)
