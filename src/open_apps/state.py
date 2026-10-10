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

import logging
import time

import requests

logger = logging.getLogger(__name__)


def safe_get_json(url: str, retries: int = 3, backoff: float = 0.5):
    """GET ``url`` and parse JSON. Returns ``[]`` on persistent request failure.

    Transient failures (connection blips, a server that is still starting)
    are retried ``retries`` times with exponential backoff (``backoff``,
    ``2 * backoff``, ... seconds). The fallback is logged as a warning --
    callers that score reward diffs off this state should treat a logged
    fallback as "probe failed", not "app is empty".

    A 4xx is not retried: it means the route is not mounted (e.g. a disabled
    app such as the online shop), and retrying would only add the full
    backoff to every state probe.
    """
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as exc:
            status = getattr(exc.response, "status_code", None)
            if status is not None and status < 500:
                logger.debug("GET %s returned %s; returning []", url, status)
                return []
            last_exc = exc
            if attempt < retries - 1:
                time.sleep(backoff * (2**attempt))
    logger.warning(
        "GET %s failed after %d attempt(s): %s; returning []",
        url,
        retries,
        last_exc,
    )
    return []


def get_current_state(url: str) -> dict:
    """Fetch the current cross-app state from a running OpenApps server.

    Args:
        url: The base URL of the OpenApps server (no trailing slash).

    Returns:
        Dict keyed by app name (todo, calendar, map, messenger,
        codeeditor, ui_library, openbanking, online_shop) whose values are the
        JSON the corresponding ``/<app>_all`` endpoints return.
    """
    state: dict = {}
    state["todo"] = safe_get_json(url + "/todo_all")
    state["calendar"] = safe_get_json(url + "/calendar_all")
    state["map"] = safe_get_json(url + "/maps/landmarks")
    state["messenger"] = safe_get_json(url + "/messages_all")
    state["codeeditor"] = safe_get_json(url + "/codeeditor_all")
    state["ui_library"] = safe_get_json(url + "/uilibrary_all")
    state["openbanking"] = safe_get_json(url + "/openbanking_all") or {}
    try:
        state["online_shop"] = safe_get_json(url + "/onlineshop_all")
    except Exception:
        state["online_shop"] = []
    return state
