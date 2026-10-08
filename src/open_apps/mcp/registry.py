"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

App-registry metadata: app-key -> URL path / config-dir, plus Hydra
variant discovery. Single source of truth — consumers import from here
rather than maintaining their own copies.

Light by design (only stdlib + ``open_apps.paths``); does not import
hydra/uvicorn/playwright, so it is safe to import from anywhere.
"""

from __future__ import annotations

import warnings

from open_apps import config_dir


# ---------------------------------------------------------------------------
# App-key -> server metadata.

APP_URL_PATHS: dict[str, str] = {
    "todo": "/todo",
    "calendar": "/calendar",
    "messages": "/messages",
    "codeeditor": "/codeeditor/",
    "map": "/maps",
    "openbanking": "/openbanking",
}

APP_CONFIG_DIRS: dict[str, str] = {
    "todo": "todo",
    "calendar": "calendar",
    "messages": "messenger",
    "codeeditor": "code_editor",
    "map": "maps",
    "openbanking": "openbanking",
}


def url_path_for(app_name: str) -> str:
    """URL path the FastHTML server exposes for an app key."""
    return APP_URL_PATHS.get(app_name, f"/{app_name}")


def config_dir_for(app_name: str) -> str:
    """``config/apps/<dir>`` name for an app key (differs from the key for messages/map/codeeditor)."""
    return APP_CONFIG_DIRS.get(app_name, app_name)


# ---------------------------------------------------------------------------
# Legacy ``appearance`` group (removed — kept resolvable for one release).

# ``appearance`` conflated global look with per-app structure, so the split is
# not one-to-one: five stems became shared themes, three became per-app
# layouts. Same mapping the docs migration table publishes.
APPEARANCE_MIGRATION: dict[str, tuple[str, str]] = {
    "default": ("theme", "default"),
    "dark_theme": ("theme", "dark"),
    "black_and_white": ("theme", "mono"),
    "challenging_font": ("theme", "challenging_font"),
    "colorblind_access": ("theme", "colorblind"),
    "kanban_board": ("layout", "kanban_board"),
    "broken_logos": ("layout", "broken_logos"),
    "clickable_logos": ("layout", "clickable_logos"),
}


def migrate_appearance(
    appearance: str,
    *,
    theme: str | None = None,
    layout: str | None = None,
) -> tuple[str | None, str | None]:
    """Translate a legacy ``appearance`` stem into ``(theme, layout)``.

    ``theme``/``layout`` are whatever the caller passed alongside it. A
    disagreement raises rather than picking a winner silently — the caller
    cannot tell from the result which of the two was applied.
    """
    try:
        group, stem = APPEARANCE_MIGRATION[appearance]
    except KeyError:
        raise ValueError(
            f"unknown appearance variant {appearance!r}. The appearance group "
            f"was replaced by a shared theme + per-app layout; known legacy "
            f"values are {', '.join(sorted(APPEARANCE_MIGRATION))}."
        ) from None

    passed = {"theme": theme, "layout": layout}[group]
    if passed is not None and passed != stem:
        raise ValueError(
            f"appearance={appearance!r} maps to {group}={stem!r}, which "
            f"conflicts with {group}={passed!r} passed alongside it. Drop the "
            f"deprecated appearance argument."
        )

    warnings.warn(
        f"appearance={appearance!r} is deprecated and will be removed; "
        f"pass {group}={stem!r} instead.",
        DeprecationWarning,
        stacklevel=3,
    )
    return (stem, layout) if group == "theme" else (theme, stem)


def list_variants(app_name: str, group: str) -> list[str]:
    """List Hydra variant yamls for a group (``theme``/``layout``/``content``).

    Returns a sorted list of variant stems (without ``.yaml``).
    ``"default"`` is forced to index 0 when present so it has a stable
    sampling identity. Returns ``["default"]`` if the group dir is
    missing.

    ``theme`` is a *shared* group (``config/apps/theme/``) applying to every
    app, so ``app_name`` is ignored for it; all other groups are per-app.

    Raises ``ValueError`` for the removed ``appearance`` group: its directory
    is gone, so the generic missing-dir path would answer ``["default"]`` and
    a caller sampling variations would silently lose every non-default one.
    """
    if group == "appearance":
        raise ValueError(
            "the appearance group was removed; list_variants(app, 'theme') "
            "for the shared design-token themes and "
            "list_variants(app, 'layout') for this app's structure variants."
        )
    if group == "theme":
        group_dir = config_dir() / "apps" / "theme"
    else:
        group_dir = config_dir() / "apps" / config_dir_for(app_name) / group
    if not group_dir.is_dir():
        return ["default"]
    stems = sorted(p.stem for p in group_dir.glob("*.yaml"))
    if "default" in stems:
        stems.remove("default")
        return ["default"] + stems
    return stems or ["default"]
