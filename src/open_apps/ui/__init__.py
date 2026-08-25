"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

Shared UI primitives.

Every app in this repo styles itself independently today -- some from Tailwind
and daisyUI over a CDN, some from a per-app ``:root`` block built out of Hydra
config, one from design tokens. That means a change to "how a button looks"
has five implementations, and four of them stop working on a host with no
outbound network.

This package is the shared alternative, in three layers:

* **tokens** live in ``open_apps.theme`` and the ``config/apps/theme/*.yaml``
  files. Not redefined here -- there is one token system, not two.
* **atoms** (:mod:`open_apps.ui.atoms`) are the smallest styled elements:
  surfaces, text, buttons. They carry no layout opinion.
* **molecules** (:mod:`open_apps.ui.molecules`) compose atoms into the
  recognisable pieces of a UI: a toolbar, a menu, an app tile.

Everything renders server-side as FastHTML elements. The only client-side
dependency is htmx, which is vendored (see ``open_apps.frontend``). No build
step, no bundler, no npm, nothing fetched at page load.

Components emit class names; :func:`open_apps.ui.styles.component_styles`
defines those classes purely in terms of ``var(--token)``. That split is what
lets a theme swap -- or the light/dark toggle -- repaint every component
without re-rendering any Python.

Usage::

    from open_apps.ui import Surface, Toolbar, component_styles

    Div(component_styles(), Toolbar(...), Surface(...))
"""
from open_apps.ui.atoms import (
    Badge,
    Divider,
    IconButton,
    Stack,
    Surface,
    Text,
    UIButton,
)
from open_apps.ui.molecules import (
    AppTile,
    Clock,
    LauncherItem,
    LauncherMenu,
    ModeToggle,
    Toolbar,
    WeatherChip,
)
from open_apps.ui.brand import Wordmark
from open_apps.ui.styles import component_styles

__all__ = [
    # atoms
    "Badge",
    "Divider",
    "IconButton",
    "Stack",
    "Surface",
    "Text",
    "UIButton",
    # molecules
    "AppTile",
    "Clock",
    "LauncherItem",
    "LauncherMenu",
    "ModeToggle",
    "Toolbar",
    "WeatherChip",
    # brand
    "Wordmark",
    # styles
    "component_styles",
]
