"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

Inline SVG icons.

An enum of icon names plus their geometry, rendered straight into the page as
``<svg>``. No icon font, no sprite sheet, no CDN — the apps have to render
correctly on a host with no outbound network, and an icon that silently fails
to load is worse than no icon at all (it leaves an empty box where an agent
expects an affordance).

Two conventions make these work with the design-token themes:

* ``stroke="currentColor"`` — icons inherit the surrounding text colour, so
  they follow whatever theme is active, including a live token swap.
* ``aria-hidden="true"`` — icons are decorative. Anything visible to the
  accessibility tree becomes an extra node in the agent's observation, so a
  purely ornamental glyph must not appear there.

Usage::

    from src.open_apps.icons import Icon, icon

    icon(Icon.FILE)                 # 16px, inherits colour
    icon(Icon.CHEVRON, size=14)
"""
from __future__ import annotations

from enum import Enum

from fasthtml.common import NotStr


class Icon(str, Enum):
    """Every icon the apps may render. Add geometry to ``_GEOMETRY`` below."""

    CHEVRON = "chevron"
    FILE = "file"
    FOLDER = "folder"
    FOLDER_OPEN = "folder-open"
    # Desktop shell
    PIN = "pin"
    PIN_FILLED = "pin-filled"
    APPS = "apps"
    CLOCK = "clock"
    CLOSE = "close"
    # Light/dark mode toggle
    SUN = "sun"
    MOON = "moon"
    # Weather, keyed off the condition string in the desktop config
    CLOUD = "cloud"
    CLOUD_RAIN = "cloud-rain"


# Path geometry on a 24x24 grid, drawn as strokes rather than fills. Stroked
# outlines read as slimmer and less heavy than a solid glyph at sidebar sizes,
# which is the point: the previous caret was a filled Unicode triangle (U+25B6)
# and looked blunt and over-angled next to text.
_GEOMETRY: dict[Icon, str] = {
    # A thin right-pointing chevron. Rotated 90deg by CSS when expanded, so one
    # icon covers both states and there is no glyph swap on toggle.
    Icon.CHEVRON: '<path d="m9.5 5.5 6.5 6.5-6.5 6.5"/>',
    # Document with a folded corner.
    Icon.FILE: (
        '<path d="M13.5 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8.5z"/>'
        '<path d="M13.5 3v5.5H19"/>'
    ),
    Icon.FOLDER: (
        '<path d="M4 6.5A1.5 1.5 0 0 1 5.5 5h3.4l1.6 2h8A1.5 1.5 0 0 1 20 8.5v9'
        'a1.5 1.5 0 0 1-1.5 1.5h-13A1.5 1.5 0 0 1 4 17.5z"/>'
    ),
    Icon.FOLDER_OPEN: (
        '<path d="M4 7A1.5 1.5 0 0 1 5.5 5.5h3.4l1.6 2h8A1.5 1.5 0 0 1 20 9"/>'
        '<path d="M3.6 10.5h17.2l-1.9 7.1a1.5 1.5 0 0 1-1.45 1.1H5.5a1.5 1.5 0 0 1-1.45-1.1z"/>'
    ),
    # --- Desktop shell ------------------------------------------------------
    # A pushpin, not a map pin. The affordance is "fasten this to the desktop",
    # and a map marker reads as "location" — wrong verb for the same glyph.
    Icon.PIN: (
        '<path d="M15 3H9l1 6-3 2v2h10v-2l-3-2z"/>'
        '<path d="M12 13v8"/>'
    ),
    # The pinned state. Filled rather than a different shape: the two states
    # must be recognisable as the same control, and weight reads faster than
    # form at 16px. fill overrides the svg-level fill="none".
    Icon.PIN_FILLED: (
        '<path d="M15 3H9l1 6-3 2v2h10v-2l-3-2z" fill="currentColor"/>'
        '<path d="M12 13v8"/>'
    ),
    # Launcher button. A 2x2 grid of tiles, echoing the desktop it opens.
    Icon.APPS: (
        '<rect x="4" y="4" width="7" height="7" rx="1.5"/>'
        '<rect x="13" y="4" width="7" height="7" rx="1.5"/>'
        '<rect x="4" y="13" width="7" height="7" rx="1.5"/>'
        '<rect x="13" y="13" width="7" height="7" rx="1.5"/>'
    ),
    Icon.CLOCK: (
        '<circle cx="12" cy="12" r="8.5"/>'
        '<path d="M12 7.5V12l3 2"/>'
    ),
    Icon.CLOSE: '<path d="M6 6l12 12M18 6L6 18"/>',
    # --- Light/dark toggle --------------------------------------------------
    Icon.SUN: (
        '<circle cx="12" cy="12" r="4"/>'
        '<path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4'
        'M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>'
    ),
    Icon.MOON: '<path d="M20 14.5A8.5 8.5 0 0 1 9.5 4a8.5 8.5 0 1 0 10.5 10.5z"/>',
    # --- Weather ------------------------------------------------------------
    # Selected by the condition string in the desktop config, never by a
    # network lookup — see config/apps/start_page/layout/desktop.yaml.
    Icon.CLOUD: (
        '<path d="M17.5 18a4.5 4.5 0 0 0 .5-8.97A6 6 0 0 0 6.1 10.5'
        'A3.75 3.75 0 0 0 6.5 18z"/>'
    ),
    Icon.CLOUD_RAIN: (
        '<path d="M17.5 15a4.5 4.5 0 0 0 .5-8.97A6 6 0 0 0 6.1 7.5'
        'A3.75 3.75 0 0 0 6.5 15z"/>'
        '<path d="M8 18v2M12 18v2.5M16 18v2"/>'
    ),
}

_STROKE_WIDTH = "1.5"


def icon_markup(name: Icon | str, size: int = 16, cls: str = "") -> str:
    """Return the raw ``<svg>`` string for ``name``.

    Raises ``KeyError`` for an unknown icon rather than rendering nothing —
    a missing glyph is a layout bug that should fail loudly in tests, not a
    blank space discovered later in a screenshot.
    """
    key = Icon(name)
    class_attr = f' class="{cls}"' if cls else ""
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"'
        f' width="{size}" height="{size}"{class_attr}'
        f' fill="none" stroke="currentColor" stroke-width="{_STROKE_WIDTH}"'
        f' stroke-linecap="round" stroke-linejoin="round"'
        f' aria-hidden="true" focusable="false">{_GEOMETRY[key]}</svg>'
    )


def icon(name: Icon | str, size: int = 16, cls: str = "") -> NotStr:
    """``icon_markup`` wrapped so FastHTML emits it as markup, not escaped text."""
    return NotStr(icon_markup(name, size=size, cls=cls))
