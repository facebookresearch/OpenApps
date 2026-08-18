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
