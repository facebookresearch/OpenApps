"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

Lightweight HTTP probe of the running OpenApps server's cross-app state.

Pulled out of ``open_apps.tasks.add_tasks_to_browsergym`` so it can be
imported by the runtime SDK without dragging in playwright / wandb /
browsergym. The browsergym module re-exports ``get_current_state`` for
backwards compatibility.
"""

from __future__ import annotations

import requests


def safe_get_json(url: str):
    """GET ``url`` and parse JSON. Returns ``[]`` on any request failure."""
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException:
        return []


def get_current_state(url: str) -> dict:
    """Fetch the current cross-app state from a running OpenApps server.

    Args:
        url: The base URL of the OpenApps server (no trailing slash).

    Returns:
        Dict keyed by app name (todo, calendar, map, messenger,
        codeeditor, online_shop) whose values are the JSON the
        corresponding ``/<app>_all`` endpoints return.
    """
    state: dict = {}
    state["todo"] = safe_get_json(url + "/todo_all")
    state["calendar"] = safe_get_json(url + "/calendar_all")
    state["map"] = safe_get_json(url + "/maps/landmarks")
    state["messenger"] = safe_get_json(url + "/messages_all")
    state["codeeditor"] = safe_get_json(url + "/codeeditor_all")
    try:
        state["online_shop"] = safe_get_json(url + "/onlineshop_all")
    except Exception:
        state["online_shop"] = []
    return state
