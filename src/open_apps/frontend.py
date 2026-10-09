"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

Locally-served frontend headers.

``fast_app()`` and ``FastHTML()`` default to loading htmx, Pico and three
helper scripts from ``cdn.jsdelivr.net``. That is fine on a laptop and useless
on an eval node with no outbound network, where the assets never arrive and
nothing says so:

* without htmx, every ``hx-*`` attribute in every app is inert — a checkbox
  still toggles visually, because that is the browser's own behaviour, but no
  request is sent and no server state changes, so the task scores zero;
* without Pico, every page renders unstyled, which for a screenshot-scored
  agent changes the observation itself.

Every app therefore constructs its FastHTML instance with
``default_hdrs=False`` and takes its headers from here instead. The files live
in ``apps/assets/vendor/`` and are served by the static route in
``apps/start_page/helper.py``.

Usage::

    from open_apps.frontend import local_hdrs

    app, rt = fast_app(default_hdrs=False, hdrs=local_hdrs())

``default_hdrs=False`` is not optional. Omit it and FastHTML prepends the CDN
tags anyway, the page works on a laptop, and the regression only shows up as a
run of zero-reward episodes on the cluster.
"""
from __future__ import annotations

from fasthtml.common import Link, Meta, Script

# Pinned by filename. FastHTML's default pulled `@picocss/pico@latest`, which
# made the styling of an eval run depend on the day it ran.
HTMX_FILENAME = "htmx-2.0.4.min.js"
PICO_FILENAME = "pico-2.1.1.min.css"

VENDOR_URL = "/assets/vendor"

HTMX_URL = f"{VENDOR_URL}/{HTMX_FILENAME}"
PICO_URL = f"{VENDOR_URL}/{PICO_FILENAME}"


def local_hdrs(pico: bool = True, htmx: bool = True) -> list:
    """Return the header elements FastHTML would otherwise load from a CDN.

    Args:
        pico: include the Pico baseline stylesheet. Pass ``False`` for an app
            that brings its own full stylesheet and only needs the behaviour.
        htmx: include htmx. Effectively always wanted — every app in this repo
            drives its state changes through ``hx-*`` attributes — but kept
            explicit so a static page can opt out.

    Returns a fresh list each call: FastHTML mutates the ``hdrs`` list it is
    given, so a shared module-level constant would accumulate one app's headers
    onto the next.
    """
    # `default_hdrs=False` drops FastHTML's charset and viewport metas along
    # with the CDN tags, so they are re-added here. The viewport one is not
    # cosmetic: without it a real phone lays the page out at 980px and then
    # scales it down, so every `max-width` media query in ui/styles.py
    # evaluates against 980 and the phone gets the desktop layout, shrunk.
    # Headless Chromium ignores the tag unless mobile emulation is on, which is
    # exactly why this regression would never show up in an eval run.
    hdrs = [
        Meta(charset="utf-8"),
        Meta(name="viewport", content="width=device-width, initial-scale=1, viewport-fit=cover"),
    ]
    if pico:
        hdrs.append(Link(rel="stylesheet", href=PICO_URL))
    if htmx:
        hdrs.append(Script(src=HTMX_URL))
    return hdrs
