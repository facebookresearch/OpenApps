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

    See :func:`_get_json` for the retry policy; this is the lenient face of
    it, for callers that would rather score an empty app than crash.
    """
    return _get_json(url, retries=retries, backoff=backoff)[0]


def _get_json(url: str, retries: int = 3, backoff: float = 0.5) -> tuple[object, bool]:
    """``(json, ok)`` for a GET of ``url``; ``([], False)`` on persistent failure.

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
            return response.json(), True
        except requests.exceptions.RequestException as exc:
            status = getattr(exc.response, "status_code", None)
            if status is not None and status < 500:
                # Not a failure: the app is simply not served, and [] is
                # exactly its state.
                logger.debug("GET %s returned %s; returning []", url, status)
                return [], True
            last_exc = exc
            if attempt < retries - 1:
                time.sleep(backoff * (2**attempt))
    logger.warning(
        "GET %s failed after %d attempt(s): %s; returning []",
        url,
        retries,
        last_exc,
    )
    return [], False


# State key -> the endpoint that serves it, in probe order.
STATE_ENDPOINTS: tuple[tuple[str, str], ...] = (
    ("todo", "/todo_all"),
    ("calendar", "/calendar_all"),
    ("map", "/maps/landmarks"),
    ("messenger", "/messages_all"),
    ("codeeditor", "/codeeditor_all"),
    ("ui_library", "/uilibrary_all"),
    ("openbanking", "/openbanking_all"),
    ("online_shop", "/onlineshop_all"),
)


def get_current_state(url: str, failed_apps: list[str] | None = None) -> dict:
    """Fetch the current cross-app state from a running OpenApps server.

    Args:
        url: The base URL of the OpenApps server (no trailing slash).
        failed_apps: When given, the state key of every app whose probe
            failed (a connection error or 5xx that outlived the retries) is
            appended to it. Such an app reads as empty in the returned state,
            which is indistinguishable from an app the agent emptied -- so
            anything that *scores* the state (rather than just rewarding it,
            as the live task does) should ask. A 4xx is not a failure: it is
            an app that is not served, and empty is its true state.

    Returns:
        Dict keyed by app name (todo, calendar, map, messenger,
        codeeditor, ui_library, openbanking, online_shop) whose values are the
        JSON the corresponding ``/<app>_all`` endpoints return.
    """
    state: dict = {}
    for key, endpoint in STATE_ENDPOINTS:
        try:
            value, ok = _get_json(url + endpoint)
        except Exception:  # noqa: BLE001 -- e.g. a body that is not JSON
            if key != "online_shop":
                raise
            # The shop's probe has always been allowed to fail open.
            value, ok = [], False
        if key == "openbanking":
            value = value or {}
        state[key] = value
        if not ok and failed_apps is not None:
            failed_apps.append(key)
    return state
