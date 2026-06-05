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

from open_apps import config_dir


# ---------------------------------------------------------------------------
# App-key -> server metadata.

APP_URL_PATHS: dict[str, str] = {
    "todo": "/todo",
    "calendar": "/calendar",
    "messages": "/messages",
    "codeeditor": "/codeeditor/",
    "map": "/maps",
}

APP_CONFIG_DIRS: dict[str, str] = {
    "todo": "todo",
    "calendar": "calendar",
    "messages": "messenger",
    "codeeditor": "code_editor",
    "map": "maps",
}


def url_path_for(app_name: str) -> str:
    """URL path the FastHTML server exposes for an app key."""
    return APP_URL_PATHS.get(app_name, f"/{app_name}")


def config_dir_for(app_name: str) -> str:
    """``config/apps/<dir>`` name for an app key (differs from the key for messages/map/codeeditor)."""
    return APP_CONFIG_DIRS.get(app_name, app_name)


def list_variants(app_name: str, group: str) -> list[str]:
    """List Hydra variant yamls for an app's group (``appearance``/``content``).

    Returns a sorted list of variant stems (without ``.yaml``).
    ``"default"`` is forced to index 0 when present so it has a stable
    sampling identity. Returns ``["default"]`` if the group dir is
    missing.
    """
    group_dir = config_dir() / "apps" / config_dir_for(app_name) / group
    if not group_dir.is_dir():
        return ["default"]
    stems = sorted(p.stem for p in group_dir.glob("*.yaml"))
    if "default" in stems:
        stems.remove("default")
        return ["default"] + stems
    return stems or ["default"]
