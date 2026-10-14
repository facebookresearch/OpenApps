"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

Open a browser on the running apps, sized like the configured device -- or
like your screen, if the device is bigger than it.

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


#: Read once from a throwaway page: the usable screen (minus menu bar / taskbar)
#: and how much of the window the browser's own toolbar and frame take up.
_MEASURE_JS = """() => ({
  left: screen.availLeft || 0, top: screen.availTop || 0,
  width: screen.availWidth, height: screen.availHeight,
  frameW: Math.max(0, outerWidth - innerWidth),
  frameH: Math.max(0, outerHeight - innerHeight),
})"""

#: Hide automation from the page, and only in this window. The chrome's
#: ``cursor.show: auto`` draws the agent cursor when ``navigator.webdriver`` is
#: set -- correct for an agent run, wrong for a person who opened a preview and
#: now has a glowing arrow chasing their own pointer.
_PERSON_DRIVING_JS = "Object.defineProperty(Navigator.prototype, 'webdriver', { get: () => false })"


def fit_to_screen(wanted: tuple[int, int], screen: dict[str, int]) -> tuple[int, int]:
    """The largest page size up to ``wanted`` whose window fits on ``screen``.

    ``screen`` is what :data:`_MEASURE_JS` returns. Each axis is clamped on its
    own: a 1920x1080 desktop on a 1440x900 laptop becomes as big as the laptop
    allows, and a phone, which already fits, is untouched.
    """
    room_w = screen["width"] - screen["frameW"]
    room_h = screen["height"] - screen["frameH"]
    return max(1, min(wanted[0], room_w)), max(1, min(wanted[1], room_h))


def _place_window(page: Any, size: tuple[int, int], screen: dict[str, int]) -> None:
    """Resize ``page``'s window so its *page area* is ``size``, centred on screen.

    ``--window-size`` can't do this: it is fixed at launch, before the screen
    can be read, and it sizes the outer window, toolbar included.
    """
    outer_w, outer_h = size[0] + screen["frameW"], size[1] + screen["frameH"]
    cdp = page.context.new_cdp_session(page)
    window = cdp.send("Browser.getWindowForTarget")
    cdp.send(
        "Browser.setWindowBounds",
        {
            "windowId": window["windowId"],
            "bounds": {
                "left": screen["left"] + max(0, (screen["width"] - outer_w) // 2),
                "top": screen["top"] + max(0, (screen["height"] - outer_h) // 2),
                "width": outer_w,
                "height": outer_h,
            },
        },
    )
    cdp.detach()


def _open_with_playwright(url: str, device: dict[str, Any]) -> bool:
    """Open a Chromium window showing ``device``, never larger than the screen.

    Two shapes, depending on whether the device needs emulation:

    * **desktop / laptop** -- a real window (``no_viewport``) sized to the
      device. The page is whatever size the window is: drag it, maximise it,
      and the layout reflows and scrolls like any browser. A fixed viewport
      here is what made a 1920x1080 desktop run off a laptop screen with no way
      to scroll -- the page was told it had 1080px of height it could not show.
    * **phone / tablet** -- ``is_mobile`` and touch only exist on a fixed
      viewport, so the device size is kept but clamped to the screen. A tablet
      taller than the display loses height, not width: the composition is the
      same and the rest is a scroll away.

    Blocks until the user closes the window, which is what keeps the browser
    alive -- Playwright tears the browser down when its context manager exits.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.debug("playwright not installed; falling back to the system browser")
        return False

    wanted = (int(device["viewport"][0]), int(device["viewport"][1]))
    # `context_kwargs` already excludes the viewport by design -- it is passed
    # separately here, and via task_kwargs.screen_resolution on the agent path.
    # Non-empty means touch, a mobile viewport or a UA: things only a fixed
    # viewport can emulate.
    context_kwargs = dict(device_module.context_kwargs(device))

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=False)
            context = browser.new_context(no_viewport=True)
            page = context.new_page()
            screen = page.evaluate(_MEASURE_JS)
            size = fit_to_screen(wanted, screen)

            if context_kwargs:
                emulated = browser.new_context(
                    viewport={"width": size[0], "height": size[1]}, **context_kwargs
                )
                # Open the new window before closing the probe's, so the
                # browser never sits at zero windows.
                emulated_page = emulated.new_page()
                context.close()
                context, page = emulated, emulated_page

            context.add_init_script(_PERSON_DRIVING_JS)
            _place_window(page, size, screen)
            if size != wanted:
                print(
                    f"Preview window fit to your screen at {size[0]}x{size[1]}; "
                    f"the {device['name']} device (what agents see) is "
                    f"{wanted[0]}x{wanted[1]}."
                )
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
