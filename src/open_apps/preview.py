"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

Open a browser on the running apps, sized like the configured device.

``uv run launch.py`` serves the apps and then blocks. This opens a window on
them so "run it and look at it" is one command instead of two plus copying a
URL out of the log.

The window is a Playwright Chromium rather than the system browser, for one
reason: ``config/device/`` sets a *viewport* and an input model, and only
Playwright can reproduce both. `webbrowser.open` can neither size a window nor
make `@media (hover: none)` match, so `launch.py device=phone` would open a
desktop-width page and the phone layout you asked to look at would not be the
thing on screen. This uses the same :func:`open_apps.device.context_kwargs`
the agent path does, so what you see is what an agent is scored on.

Never fatal. If Playwright is missing, its browsers are not installed, or the
display is unavailable, this falls back to the system browser and finally to
just printing the URL -- serving the apps is the job, opening a window is a
convenience.
"""
from __future__ import annotations

import logging
import threading
import time
import urllib.error
import urllib.request
from typing import Any

from open_apps import device as device_module

logger = logging.getLogger(__name__)

#: How long to wait for the server to answer before giving up on the window.
#: The apps take a few seconds to bind; past this something is actually wrong
#: and the traceback will be in the server's own output, not here.
_STARTUP_TIMEOUT_S = 60.0
_POLL_INTERVAL_S = 0.25


def _wait_for_server(url: str, timeout: float = _STARTUP_TIMEOUT_S) -> bool:
    """Poll ``url`` until it answers. True if it came up inside ``timeout``."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2):
                return True
        except urllib.error.HTTPError:
            # Any HTTP response means something is listening and routing.
            return True
        except Exception:
            time.sleep(_POLL_INTERVAL_S)
    return False


def _open_with_playwright(url: str, device: dict[str, Any]) -> bool:
    """Open a Chromium window emulating ``device``. True if it stayed open.

    Blocks until the user closes the window, which is what keeps the browser
    alive -- Playwright tears the browser down when its context manager exits.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.debug("playwright not installed; falling back to the system browser")
        return False

    width, height = (int(device["viewport"][0]), int(device["viewport"][1]))
    # `context_kwargs` already excludes the viewport by design -- it is passed
    # separately here, and via task_kwargs.screen_resolution on the agent path.
    context_kwargs = dict(device_module.context_kwargs(device))

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=False,
                # Ask the window to match the viewport so there is no scrollbar
                # or letterbox around the page you came to look at.
                args=[f"--window-size={width},{height}"],
            )
            context = browser.new_context(
                viewport={"width": width, "height": height}, **context_kwargs
            )
            page = context.new_page()
            page.goto(url)
            # Hold the window open. `page.wait_for_event("close")` would need a
            # timeout; polling is_closed costs nothing and has no ceiling.
            while not page.is_closed():
                time.sleep(0.5)
            browser.close()
        return True
    except Exception as exc:
        logger.debug("playwright preview unavailable (%s)", exc)
        return False


def _open_with_system_browser(url: str) -> bool:
    try:
        import webbrowser

        return webbrowser.open(url)
    except Exception:
        return False


def open_preview(url: str, device: Any = None, *, block: bool = False) -> None:
    """Open a browser on ``url`` once the server answers.

    Args:
        url: Landing page to open.
        device: The ``config/device/`` node (or None for desktop defaults).
        block: Run inline instead of on a background thread. The caller is
            normally about to block in ``serve()``, so the default spawns a
            daemon thread and returns immediately.
    """
    dev = device_module.as_dict(device)

    def _run() -> None:
        if not _wait_for_server(url):
            logger.warning("apps did not answer at %s; not opening a browser", url)
            return
        if _open_with_playwright(url, dev):
            return
        if _open_with_system_browser(url):
            # Worth saying: the whole point of the device axis is the viewport,
            # and this path cannot set one.
            print(
                f"Opened {url} in the system browser. Install Playwright's "
                f"Chromium (`uv run playwright install chromium`) to preview at "
                f"the configured {dev['name']} viewport "
                f"({dev['viewport'][0]}x{dev['viewport'][1]})."
            )
            return
        print(f"Could not open a browser. The apps are at {url}")

    if block:
        _run()
    else:
        threading.Thread(target=_run, name="openapps-preview", daemon=True).start()
